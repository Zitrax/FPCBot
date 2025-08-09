#!/usr/bin/env python3
"""
This script runs as FPCBot on Wikimedia Commons.
It counts the votes in featured picture nominations,
closes and archives finished nominations,
informs uploaders and nominators about the success
and adds newly promoted featured pictures to the gallery pages.

Programmed by Daniel78 at Commons.

The script is based on Pywikibot.  Therefore you can use it with
Pywikibot options (so-called global options); to list them,
use '-help:global' or run 'pwb.py -help'.

In addition, the script understands the following
command line arguments for tasks and (local) options:

Tasks:

-help           Print this help and exit.
-info           Print status and vote count info about current nominations.
-close          Close, count votes and add results to finished nominations.
-park           Park closed and verified nominations.
-test           Test vote counting against an old log.

Options:

-auto           Do not ask before commiting edits to articles.
-dry            Do not submit any edits, just print them.
-threads        Use threads to speed things up
                (must be used with '-auto' and/or '-dry').
-fpc            Handle the featured candidates (if neither -fpc
                nor -delist is used all candidates are handled).
-delist         Handle the delisting candidates (if neither -fpc
                nor -delist is used all candidates are handled).
-notime         Avoid displaying timestamps in log output.
-match pattern  Only operate on candidates matching this pattern.
"""

# Standard library imports
import sys
import abc
import signal
import datetime
import time
import re
import threading
import traceback

# Third-party imports
import pywikibot
from pywikibot import config


class ThreadCheckCandidate(threading.Thread):
    def __init__(self, candidate, check):
        threading.Thread.__init__(self)
        self.candidate = candidate
        self.check = check

    def run(self):
        self.check(self.candidate)


class Candidate(abc.ABC):
    """
    Abstract base class for featured picture candidates/nominations,
    bundles all common properties and methods.
    The individual candidates/nominations are represented by instances
    of the concrete subclasses.
    """

    def __init__(
        self,
        page,
        listName,
        ProR,
        ConR,
        NeuR,
        ProString,
        ConString,
        ReviewedR,
        CountedR,
        VerifiedR,
    ):
        """
        Although this is an abstract base class, the initializer is used
        to set instance variables to the passed values or to default values.

        @param page      A pywikibot.Page object for the nomination subpage.
        @param listName  A string with the name of the candidate list page.
        @param ProR      A compiled regex (re.Pattern) to find positive votes.
        @param ConR      A compiled regex (re.Pattern) to find negative votes.
        @param NeuR      A compiled regex (re.Pattern) to find neutral votes.
        @param ProString A string expressing a positive result.
        @param ConString A string expressing a negative result.
        @param ReviewedR A compiled regex (re.Pattern) for finding
                         a reviewed results template.
        @param CountedR  A compiled regex (re.Pattern) for finding
                         an unreviewed results template.
        @param VerifiedR A compiled regex (re.Pattern) used to analyse
                         the contents of a reviewed results template.
        """
        # Later perhaps this can be cleaned up by letting the subclasses
        # keep the variables or (better?!) by using class constants
        # which are adapted by the subclasses.
        self.page = page
        self._listPageName = listName
        self._pro = 0
        self._con = 0
        self._neu = 0
        self._proR = ProR  # Regexp for positive votes
        self._conR = ConR  # Regexp for negative votes
        self._neuR = NeuR  # Regexp for neutral  votes
        self._proString = ProString
        self._conString = ConString
        self._ReviewedR = ReviewedR
        self._CountedR = CountedR
        self._VerifiedR = VerifiedR
        self._votesCounted = False
        self._daysOld = -1
        self._daysSinceLastEdit = -1
        self._creationTime = None
        self._imgCount = None
        self._fileName = None
        self._alternative = None
        self._setFiles = None
        self._creator = None    # Username of the original creator
        self._uploader = {}     # Mapping: filename -> username of uploader
        self._nominator = None  # Username of the nominator

    def printAllInfo(self):
        """
        Print the name, status, vote counts and other information
        for this candidate, as part of an overview of all open candiates.
        """
        try:
            self.countVotes()
            out(
                "%s: S:%02d O:%02d N:%02d D:%02d De:%02d Se:%02d Im:%02d W:%s (%s)"
                % (
                    self.cutTitle(),
                    self._pro,
                    self._con,
                    self._neu,
                    self.daysOld(),
                    self.daysSinceLastEdit(),
                    self.sectionCount(),
                    self.imageCount(),
                    "True " if self.isWithdrawn() else "False",
                    self.statusString(),
                )
            )
        except pywikibot.exceptions.NoPageError:
            error("%s: -- No such page -- " % self.cutTitle())

    def creator(self, link):
        """
        Returns the name of the user who has originally created the image(s).
        There is no generally applicable way to determine the creator.
        Therefore nominators should use the phrase
            '{{Info}} ... created by [[User:...]]'
        on the nomination subpage in order to identify the original creator.
        We also allow the common variant 'created and <adverb?> uploaded by'.
        If this phrase is present, the method returns the username
        (if 'link' is True, a link to the user page), else just ''.
        """
        if self._creator is None:
            wikitext = self.page.get(get_redirect=True)
            if match := CreatorNameR.search(wikitext):
                self._creator = match.group(1).strip()
            else:
                self._creator = ""
        if self._creator and link:
            return user_page_link(self._creator)
        return self._creator

    def uploader(self, filename, link):
        """
        Returns the name of the user who has uploaded the original version
        of the image; if link is True, returns a link to the user page.
        (This method works differently than nominator() because all files of
        a set must have the same nominator, but can have different uploaders.)
        """
        try:
            username = self._uploader[filename]
        except KeyError:
            username = oldest_revision_user(pywikibot.Page(G_Site, filename))
            self._uploader[filename] = username
        if username:
            return user_page_link(username) if link else username
        return "Unknown"

    def nominator(self, link):
        """
        Returns the name of the user who has created the nomination;
        if link is True, returns a link to the nominator's user page.
        """
        if self._nominator is None:
            self._nominator = oldest_revision_user(self.page)
        if self._nominator:
            return user_page_link(self._nominator) if link else self._nominator
        return "Unknown"

    def isSet(self):
        """
        Check whether this candidate is a set nomination or not;
        the name of the nomination subpage for a set must contain "/[Ss]et/".
        """
        return re.search(r"/ *[Ss]et */", self.page.title()) is not None

    def setFiles(self):
        """
        Try to return a list of all nominated files in a set nomination.
        We just search for all filenames in the first <gallery>...</gallery>
        on the nomination subpage.
        If we can't identify any files the result is an empty list.
        """
        # Use cached result if possible
        if self._setFiles is not None:
            return self._setFiles
        # Get wikitext of the nomination subpage and extract
        # the contents of the <gallery>...</gallery> element
        wikitext = self.page.get(get_redirect=True)
        match = re.search(
            r"<gallery[^>]*>(.+?)</gallery>",
            wikitext,
            flags=re.DOTALL,
        )
        if not match:
            error(
                "Error - no <gallery> found in set nomination "
                f"'{self.page.title()}'"
            )
            return []
        text_inside_gallery = match.group(1)
        # As a precaution let's comb out all comments:
        text_inside_gallery = re.sub(
            r"<!--.+?-->", "", text_inside_gallery, flags=re.DOTALL
        )
        # First try to find files which are properly listed with 'File:'
        # or 'Image:' prefix; they must be the first element on their line,
        # but leading whitespace is tolerated:
        files_list = re.findall(
            r"^ *(?:[Ff]ile|[Ii]mage):([^\n|]+)",
            text_inside_gallery,
            flags=re.MULTILINE
        )
        if not files_list:
            # If we did not find a single file, let's try a casual search
            # for lines which, ahem, seem to start with an image filename:
            files_list = re.findall(
                r"^ *([^|\n:<>\[\]]+\.(?:jpe?g|tiff?|png|svg|webp|xcf))",
                text_inside_gallery,
                flags=re.MULTILINE | re.IGNORECASE,
            )
        if files_list:
            # Appyly uniform formatting to all filenames:
            files_list = [
                f"File:{filename.strip().replace('_', ' ')}"
                for filename in files_list
            ]
        else:
            error(
                "Error - no images found in set nomination "
                f"'{self.page.title()}'"
            )
        self._setFiles = files_list
        return files_list

    def findGalleryOfFile(self):
        """
        Try to find the gallery link in the nomination subpage
        in order to make the life of the closing users easier.
        """
        text = self.page.get(get_redirect=True)
        match = re.search(
            r"Gallery[^\n]+?\[\[Commons:Featured[_ ]pictures\/([^\n\]]+)",
            text,
        )
        if match is not None:
            return match.group(1)
        else:
            return ""

    def countVotes(self):
        """
        Counts all the votes for this nomination
        and subtracts eventual striked out votes
        """
        if self._votesCounted:
            return

        text = self.page.get(get_redirect=True)
        if text:
            text = filter_content(text)
            self._pro = len(re.findall(self._proR, text))
            self._con = len(re.findall(self._conR, text))
            self._neu = len(re.findall(self._neuR, text))
        else:
            error(f"Error - '{self.page.title()}' has no content")

        self._votesCounted = True

    def isWithdrawn(self):
        """Withdrawn nominations should not be counted."""
        text = self.page.get(get_redirect=True)
        text = filter_content(text)
        withdrawn = len(re.findall(WithdrawnR, text))
        return withdrawn > 0

    def isFPX(self):
        """Page marked with FPX template."""
        return len(re.findall(FpxR, self.page.get(get_redirect=True)))

    def rulesOfFifthDay(self):
        """Check if any of the rules of the fifth day can be applied."""
        if self.daysOld() < 5:
            return False

        self.countVotes()

        # First rule of the fifth day
        if self._pro <= 1:
            return True

        # Second rule of the fifth day
        if self._pro >= 10 and self._con == 0:
            return True

        # If we arrive here, no rule applies.
        return False

    def closePage(self):
        """
        Will add the voting results to the page if it is finished.
        If it was, True is returned else False
        """

        # First make sure that the page actually exists
        if not self.page.exists():
            error('"%s" Error: no such page?!' % self.cutTitle())
            ask_for_help(
                list_includes_missing_subpage.format(
                    list=self._listPageName, subpage=self.page.title()
                )
            )
            return False

        if (self.isWithdrawn() or self.isFPX()) and self.imageCount() <= 1:
            # Will close withdrawn nominations if there are more than one
            # full day since the last edit

            why = "withdrawn" if self.isWithdrawn() else "FPXed"

            oldEnough = self.daysSinceLastEdit() > 0
            out(
                '"%s" %s %s'
                % (
                    self.cutTitle(),
                    why,
                    "closing" if oldEnough else "but waiting a day",
                )
            )

            if not oldEnough:
                return False

            self.moveToLog(why)
            return True

        # We skip rule of the fifth day if we have several alternatives
        fifthDay = False if self.imageCount() > 1 else self.rulesOfFifthDay()

        if not fifthDay and not self.isDone():
            out('"%s" is still active, ignoring' % self.cutTitle())
            return False

        old_text = self.page.get(get_redirect=True)
        if not old_text:
            error('"%s" Error: has no content' % self.cutTitle())
            ask_for_help(
                f"The nomination subpage [[{self.page.title()}]] "
                f"seems to be empty. {please_fix_hint}"
            )
            return False

        if re.search(r"{{\s*FPC-closed-ignored.*}}", old_text):
            out('"%s" is marked as ignored, so ignoring' % self.cutTitle())
            return False

        if re.search(self._CountedR, old_text):
            out('"%s" needs review, ignoring' % self.cutTitle())
            return False

        if re.search(self._ReviewedR, old_text):
            out('"%s" already closed and reviewed, ignoring' % self.cutTitle())
            return False

        if self.imageCount() <= 1:
            self.countVotes()

        new_text = old_text.rstrip() + "\n\n" + self.getResultString()

        # Append a keyword for the result to the heading
        if self.imageCount() <= 1:
            new_text = self.fixHeading(new_text)

        commit(
            old_text,
            new_text,
            self.page,
            self.getCloseCommitComment()
            + (" (FifthDay=%s)" % ("yes" if fifthDay else "no")),
        )

        return True

    def fixHeading(self, text, value=None):
        """
        Appends a keyword -- '(not) featured', '(not) delisted' --
        for the result to the heading of the nomination subpage.
        Reports if the nomination does not start correctly with a heading.
        Returns the modified wikitext of the nomination subpage.

        @param text  The complete wikitext of the nomination subpage.
        @param value If specified as 'yes' or 'no' (the value of the 'featured'
            or 'delisted' parameter from the reviewed results template),
            the keyword is based on this value, otherwise we call isPassed().
        """
        # Determine the keyword
        match value:
            case "yes":
                success = True
            case "no":
                success = False
            case _:
                success = self.isPassed()
        keyword = self._proString if success else self._conString
        # Check if the nomination correctly starts with a level 3+ heading
        text = text.lstrip()  # Silently remove irritating whitespace.
        match = re.match(r"===(.+?)===", text)
        if not match:
            warn(
                f"Nomination '{self.page.title()}' does not start "
                f"with a heading; can't add '{keyword}' to the title."
            )
            ask_for_help(
                f"The nomination [[{self.page.title()}]] does not start with "
                "the usual <code><nowiki>===...===</nowiki></code> heading. "
                "Please check if there is any rubbish at the beginning "
                "and remove it, fix the heading if necessary, "
                f"and add <code>, {keyword}</code> at the end of the heading."
            )
            return text
        # Check whether the heading already contains the keyword or not
        heading = match.group(1).strip()
        if heading.endswith(keyword):
            return text
        # Add the keyword to the heading
        return text.replace(heading, f"{heading}, {keyword}", 1)

    @abc.abstractmethod
    def getResultString(self):
        """
        Returns the results template to be added when closing a nomination.
        Must be implemented by the subclasses.
        """
        pass

    @abc.abstractmethod
    def getCloseCommitComment(self):
        """
        Returns the commit comment to be used when closing a nomination.
        Must be implemented by the subclasses.
        """
        pass

    def creationTime(self):
        """
        Returns the time at which this nomination was created.
        If we can't determine the creation time, for example because
        the page has been moved without leaving a redirect etc.,
        we return the current time so that we ignore this nomination
        as too young.
        """
        if self._creationTime:
            return self._creationTime

        try:
            timestamp = self.page.oldest_revision.timestamp
        except pywikibot.exceptions.PageRelatedError:
            error(
                f"Could not ascertain creation time of '{self.page.title()}', "
                "returning now()"
            )
            return datetime.datetime.now(datetime.UTC)
        # MediaWiki timestamps are always stored in UTC,
        # but querying a revision timestamp still returns an offset-naive
        # pywikibot.Timestamp object.  Therefore we convert it right away
        # to an offset-aware datetime object in order to compare it
        # easily and correctly to offset-aware datetime objects:
        self._creationTime = timestamp.replace(tzinfo=datetime.UTC)

        # print "C:" + self._creationTime.isoformat()
        # print "N:" + datetime.datetime.now(datetime.UTC).isoformat()
        return self._creationTime

    def statusString(self):
        """Returns a short string describing the status of the candidate."""
        if reviewed := self.isReviewed():
            return reviewed
        if self.isWithdrawn():
            return "Withdrawn"
        if self.isIgnored():
            return "Ignored"
        if self.isDone() or self.rulesOfFifthDay():
            text = self._proString if self.isPassed() else self._conString
            return text.capitalize()
        return "Active"

    def daysOld(self):
        """Find the number of days this nomination has existed."""
        if self._daysOld != -1:
            return self._daysOld

        delta = datetime.datetime.now(datetime.UTC) - self.creationTime()
        self._daysOld = delta.days
        return self._daysOld

    def daysSinceLastEdit(self):
        """
        Number of whole days since last edit

        If the value can not be found -1 is returned
        """
        if self._daysSinceLastEdit != -1:
            return self._daysSinceLastEdit

        try:
            timestamp = self.page.latest_revision.timestamp
        except pywikibot.exceptions.PageRelatedError:
            return -1
        # MediaWiki timestamps are always stored in UTC,
        # but querying a revision timestamp still returns an offset-naive
        # pywikibot.Timestamp object.  Therefore we convert it right away
        # to an offset-aware datetime object in order to compare it
        # easily and correctly to offset-aware datetime objects:
        last_edit = timestamp.replace(tzinfo=datetime.UTC)

        delta = datetime.datetime.now(datetime.UTC) - last_edit
        self._daysSinceLastEdit = delta.days
        return self._daysSinceLastEdit

    def isDone(self):
        """
        Checks if a nomination can be closed
        """
        return self.daysOld() >= 9

    def isPassed(self):
        """
        Find if an image can be featured.
        Does not check the age, it needs to be
        checked using isDone()
        """

        if self.isWithdrawn():
            return False

        if not self._votesCounted:
            self.countVotes()

        return self._pro >= 7 and (self._pro >= 2 * self._con)

    def isReviewed(self):
        """
        Returns a short string for use with statusString(),
        indicating whether the nomination has already been closed and reviewed
        or has been closed and counted, but is still waiting for the review;
        if neither the one nor the other applies, returns False.
        """
        wikitext = self.page.get(get_redirect=True)
        if self._ReviewedR.search(wikitext):
            return "Reviewed"
        if self._CountedR.search(wikitext):
            return "Counted"
        return False

    def isIgnored(self):
        """Some nominations currently require manual check."""
        return self.imageCount() != 1

    def sectionCount(self):
        """Counts the number of sections in this nomination."""
        text = self.page.get(get_redirect=True)
        text = filter_content(text)  # Ignore commented, stricken etc. stuff.
        return len(SectionR.findall(text))

    def imageCount(self):
        """
        Counts the number of images in this nomination.
        Ignores small images which are below a certain threshold
        as they probably are just inline icons and not alternatives.
        """
        if self._imgCount is not None:
            return self._imgCount
        text = self.page.get(get_redirect=True)
        text = filter_content(text)  # Ignore commented, stricken etc. stuff.
        images = ImagesR.findall(text)
        count = len(images)
        if count >= 2:
            # We have several images, check if some of them are marked
            # as thumbnails or are too small to be counted
            for image_link, _ in images:
                if ImagesThumbR.search(image_link):
                    count -= 1
                else:
                    size = ImagesSizeR.search(image_link)
                    if size and (int(size.group(1)) <= 150):
                        count -= 1
        self._imgCount = count
        return count

    def existingResult(self):
        """
        Scans the nomination subpage of this candidate and tries to find
        and parse the results of the nomination.
        Returns either an empty list (if the nomination was not closed
        or does not use one of the usual formats for the results)
        or a list of tuples; normally it should contain just a single tuple.
        The length of the tuple varies, depending on the results format,
        but only the first four values of the tuple are important
        for the comparison of the results:
        [0] count of support votes,
        [1] count of oppose votes,
        [2] count of neutral votes,
        [3] ('yes'|'no'|'featured'|'not featured').
        """
        text = self.page.get(get_redirect=True)
        # Search first for result(s) using the new template-base format,
        # and if this fails for result(s) in the old text-based format:
        results = re.findall(VerifiedResultR, text)
        if not results:
            results = re.findall(PreviousResultR, text)
        return results

    def compareResultToCount(self):
        """
        If there is an existing result we compare it to a new vote count
        made by this bot and check whether they match or not.
        This is useful to test the vote counting code of the bot
        and to find possibly incorrect old results.
        """
        res = self.existingResult()

        if self.isWithdrawn():
            out("%s: (ignoring, was withdrawn)" % self.cutTitle())
            return
        elif self.isFPX():
            out("%s: (ignoring, was FPXed)" % self.cutTitle())
            return
        elif self.imageCount() > 1:
            out("%s: (ignoring, contains alternatives)" % self.cutTitle())
            return
        elif not res:
            out("%s: (ignoring, has no results)" % self.cutTitle())
            return
        elif len(res) > 1:
            out("%s: (ignoring, has several results)" % self.cutTitle())
            return

        # We have one result, so make a vote count and compare
        old_res = res[0]
        was_featured = old_res[3].lower() in {"yes", "featured"}
        ws = int(old_res[0])
        wo = int(old_res[1])
        wn = int(old_res[2])
        self.countVotes()

        if (
            self._pro == ws
            and self._con == wo
            and self._neu == wn
            and was_featured == self.isPassed()
        ):
            status = "OK"
        else:
            status = "FAIL"

        # List info to console
        out(
            "%s: S%02d/%02d O:%02d/%02d N%02d/%02d F%d/%d (%s)"
            % (
                self.cutTitle(),
                self._pro,
                ws,
                self._con,
                wo,
                self._neu,
                wn,
                self.isPassed(),
                was_featured,
                status,
            )
        )

    def cutTitle(self):
        """Returns a fixed width title for the nomination."""
        title = self.subpageName(keep_prefix=False, keep_number=True)
        # We skip 'removal/', 'File:' etc., but 'Set/' is informative
        if self.isSet():
            title = f"Set/{title}"
        return title[0:50].ljust(50)

    def fileName(self):
        """
        Returns the filename of this candidate
        (for set nominations, use setFiles() instead).
        """
        # Try the selected alternative or a cached result first
        if self._alternative:
            return self._alternative
        if self._fileName:
            return self._fileName

        # Try to derive the filename from the name of the nomination subpage,
        # using the standard 'File:' namespace
        filename = PrefixR.sub("File:", self.page.title())
        filename = re.sub(r" */ *\d+ *$", "", filename)  # Remove '/2' etc.

        # If there is no file with that name, use the name of the first image
        # on the nomination subpage instead
        if not pywikibot.Page(G_Site, filename).exists():
            if match := ImagesR.search(self.page.get(get_redirect=True)):
                filename = match.group(2)

        # Check if the image was renamed and try to resolve the redirect
        page = pywikibot.Page(G_Site, filename)
        if page.exists() and page.isRedirectPage():
            filename = page.getRedirectTarget().title()
        # TODO: Add more tests, catch exceptions, report missing files, etc.!

        filename = filename.replace('_', ' ')
        self._fileName = filename
        return filename

    def subpageName(self, keep_prefix=True, keep_number=True):
        """
        Returns the name of the nomination subpage for this candidate
        without the leading 'Commons:Featured picture candidates/'
        (if you want to include it, just call 'self.page.title()' instead).

        Use 'keep_number=True' and adjust the 'keep_prefix' parameter
        to get tailor-made values for the 'com-nom' and 'subpage' parameters
        of the {{Assessments}} and user notification templates:
        for {{Assessments}}, pass 'keep_prefix=False' to remove the 'Set/',
        'removal/', and 'File:'/'Image:' prefixes (plus their combinations);
        for the user notification templates, pass 'keep_prefix=True' to keep
        these parts without any normalization.

        Use 'keep_prefix=False, keep_number=False' to get a clean title
        for the nomination, e.g. as title for a set nomination.
        """
        name = self.page.title()
        name = name.replace('_', ' ')
        name = re.sub(wikipattern(candPrefix), "", name, count=1).strip()
        if not keep_prefix:
            name = re.sub(
                r"^(?:[Rr]emoval */ *)?(?:[Ss]et */|(?:[Ff]ile|[Ii]mage) *:) *",
                "",
                name,
                count=1,
            )
        if not keep_number:  # Remove trailing '.../2' etc. of repeated noms.
            name = re.sub(r" */ *\d+ *$", "", name, count=1)
        return name

    def addToFeaturedList(self, gallery, files):
        """
        Adds the new featured picture to the list with recently
        featured images that is used on the FP landing page.
        This method uses just the basic gallery name, like 'Animals'.
        Should only be called on closed and verified candidates.

        This is ==STEP 1== of the parking procedure.

        @param gallery The basic gallery name, like 'Animals'.
        @param files List with filename(s) of the featured picture or set.
        """
        file = files[0]  # For set nominations just use the first file.
        listpage = "Commons:Featured pictures, list"
        page = pywikibot.Page(G_Site, listpage)
        old_text = page.get(get_redirect=True)

        # First check if the image is already on the page.
        # This can happen if the process has previously been interrupted.
        if re.search(wikipattern(file), old_text):
            out(
                "Skipping addToFeaturedList() for '%s', "
                "image is already listed." % file
            )
            return

        # This function first needs to find the gallery
        # then inside the gallery tags remove the last line and
        # add this candidate to the top
        # Thanks KODOS for a nice regexp gui
        # This adds ourself first in the list of length 4 and removes the last
        # all in the chosen gallery
        out("Looking for gallery: '%s'" % wikipattern(gallery))
        ListPageR = re.compile(
            r"(^==\s*{{{\s*\d+\s*\|%s\s*}}}\s*==\s*<gallery.*>\s*)(.*\s*)(.*\s*.*\s*)(.*\s*)(</gallery>)"
            % wikipattern(gallery),
            re.MULTILINE,
        )
        new_text = re.sub(ListPageR, r"\1%s\n\2\3\5" % file, old_text)
        commit(old_text, new_text, page, "Added [[%s]]" % file)

    def addToGalleryPage(self, gallery_link, files):
        """
        Adds the new featured picture (resp. all files from a set nomination)
        to the appropriate featured picture gallery page.
        Should only be called on closed and verified candidates.

        This is ==STEP 2== of the parking procedure.

        @param gallery_link The gallery link with the name of the gallery page
        and (optionally) a section anchor which denotes the target section
        on that page.
        @param files List with filename(s) of the featured picture or set.
        """
        subpage_name = self.page.title()

        # Replace all underscores and non-breaking spaces by plain spaces
        # (underscores are present if users just copy the gallery link,
        # NBSP can be entered by accident with some keyboard settings,
        # e.g. on macOS or Linux)
        gallery_link = gallery_link.replace("_", " ").replace("\u00A0", " ")
        # Split the gallery link into gallery page name and section anchor
        # (the latter can be empty)
        link_parts = gallery_link.split("#", maxsplit=1)
        gallery_page_name = link_parts[0].strip()
        section = link_parts[1].strip() if len(link_parts) > 1 else ""

        # Read the gallery page
        full_page_name = f"Commons:Featured pictures/{gallery_page_name}"
        page = pywikibot.Page(G_Site, full_page_name)
        try:
            old_text = page.get(get_redirect=False)
        except pywikibot.exceptions.NoPageError:
            error(f"Error - gallery page '{full_page_name}' does not exist.")
            ask_for_help(
                f"The gallery page [[{full_page_name}]] which was specified "
                f"by the nomination [[{subpage_name}]] does not exist. "
                f"{please_check_gallery_and_sort_fps}"
            )
            return
        except pywikibot.exceptions.PageRelatedError as exc:
            error(f"Error - can't read gallery page '{full_page_name}': {exc}")
            ask_for_help(
                "The bot could not read the gallery page "
                f"[[{full_page_name}]] which was specified "
                f"by the nomination [[{subpage_name}]]: {exc} "
                f"{please_check_gallery_and_sort_fps}"
            )
            return

        # Check if some files are already on the page.
        # This can happen if the process has previously been interrupted.
        # We skip these files but handle any file which is not yet present.
        new_files = [
            file for file in files
            if not re.search(wikipattern(file), old_text)
        ]
        if not new_files:
            # Not a single file needs to be added, so we can stop here.
            out(
                f"Skipping addToGalleryPage() for '{subpage_name}', "
                "image(s) already listed."
            )
            return
        # Format the new entries and a summary for the message
        new_entries = "".join(
            f"{filename}|{bare_filename(filename)}\n"
            for filename in new_files
        )
        files_for_msg = f"[[{new_files[0]}]]"
        if len(new_files) > 1:
            files_for_msg += f" and {len(new_files) - 1} more set file(s)"

        # Have we got a section anchor?
        if section:
            # Search for the target section, i.e. a (sub)heading followed
            # by the associated <gallery>...</gallery> element,
            # separated by at most a single line (e.g. a 'See also' hint)
            section_pattern = (
                r"(\n=+ *"
                + re.escape(section)  # Escape chars with regex meaning.
                + r" *=+ *\n+(?:[^<= \n][^\n]+\s+)?<gallery\b.+?)</gallery>"
            )
            match = re.search(section_pattern, old_text, flags=re.DOTALL)
            # Now match is a valid match object if we have found
            # the section, else it is None.
        else:
            # There was no section anchor, so there is no match
            match = None

        # Add the new file(s) to the gallery page
        if match is not None:
            # Append the new file(s) to the target section:
            new_text = (
                old_text[:match.end(1)]
                + new_entries
                + old_text[match.end(1):]
            )
            message = (
                f"Added {files_for_msg} to section '{section}'"
            )
        else:
            # Either the section anchor was missing or empty,
            # or we did not find the matching target section.
            # Append the new file(s) to the 'Unsorted' section;
            # it should be just the last <gallery></gallery> on the page.
            gallery_end_pos = old_text.rfind("</gallery>")
            if gallery_end_pos < 0:
                # Ouch, the page does not contain a single <gallery></gallery>
                error(
                    "Error - found no 'Unsorted' section on "
                    f"'{full_page_name}', can't add '{new_files[0]}'."
                )
                ask_for_help(
                    f"The gallery page [[{full_page_name}]] which was "
                    f"specified by the nomination [[{subpage_name}]] "
                    "seems to be invalid or broken. "
                    f"{please_check_gallery_and_sort_fps}"
                )
                return
            new_text = (
                old_text[:gallery_end_pos]
                + new_entries
                + old_text[gallery_end_pos:]
            )
            message = f"Added {files_for_msg} to the 'Unsorted' section"
            warn(
                f"{'Invalid' if section else 'No'} gallery section, "
                "adding image(s) to the 'Unsorted' section."
            )
            problem = (
                f"does not point to a valid section on [[{full_page_name}]]. "
                "(The section after the <code>#</code> in a gallery link "
                "is valid if and only if it corresponds letter for letter "
                "to a subheading which is immediately followed "
                "by a <code><nowiki><gallery></nowiki></code> element.)"
                if section else
                f"does not specify the section on [[{full_page_name}]] "
                "to which the image(s) should be added."
            )
            ask_for_help(
                f"The gallery link ''{gallery_link}'' in the nomination "
                f"[[{subpage_name}]] {problem} "
                "Therefore one or more new featured pictures are added "
                f"to the ''Unsorted'' section of [[{full_page_name}]]. "
                "Please sort these images into the correct section."
            )
        commit(old_text, new_text, page, message)

    def addAssessments(self, files):
        """
        Adds the {{Assessments}} template to the description page
        of a featured picture, resp. to all files in a set.
        Should only be called on closed and verified candidates.

        This is ==STEP 3== of the parking procedure.

        @param files List with filename(s) of the featured picture or set.
        """
        AssR = re.compile(r"\{\{\s*[Aa]ssessments\s*(\|.*?)\}\}")
        subpage_name = self.subpageName(keep_prefix=False, keep_number=True)
        for filename in files:
            page = pywikibot.Page(G_Site, filename)
            current_page = page
            old_text = page.get(get_redirect=True)

            # Is there already an {{Assessments}} template for this file?
            if match := re.search(AssR, old_text):
                # There is already an {{Assessments}} template, so update it.
                # We must remove any existing 'featured', 'com-nom', 'subpage'
                # parameters because they are probably outdated.
                # TODO: 'subpage' is an old name of 'com-nom', remove it later.
                params = re.sub(r"\|\s*featured\s*=\s*\d+", "", match.group(1))
                params = re.sub(r"\|\s*(?:subpage|com-nom)\s*=\s*[^{}|]+", "", params)
                params = params.strip()  # Required by the following test.
                if params and params[0] != "|":
                    params = "|" + params
                params += "|featured=1|com-nom=" + subpage_name
                new_text = (
                    old_text[:match.start(0)]
                    + "{{Assessments%s}}" % params
                    + old_text[match.end(0):]
                )
                if new_text == old_text:
                    # Old and new template are identical, so skip this file,
                    # but continue to check other files (for set nominations)
                    out(
                        "Skipping addAssessments() for '%s', "
                        "image is already featured." % filename
                    )
                    continue
            else:
                # There is no {{Assessments}} template, so just add a new one
                if re.search(r"\{\{(?:|\s*)[Ll]ocation", old_text):
                    end = findEndOfTemplate(old_text, "[Ll]ocation")
                elif re.search(r"\{\{(?:|\s*)[Oo]bject[_\s][Ll]ocation", old_text):
                    end = findEndOfTemplate(old_text, r"[Oo]bject[_\s][Ll]ocation")
                else:
                    end = findEndOfTemplate(old_text, "[Ii]nformation")
                new_text = (
                    old_text[:end]
                    + "\n{{Assessments|featured=1|com-nom=%s}}\n" % subpage_name
                    + old_text[end:]
                )
            commit(old_text, new_text, current_page, "FP promotion")

    def addToCurrentMonth(self, files):
        """
        Adds the candidate to the monthly overview of new featured pictures.
        Should only be called on closed and verified candidates.

        This is ==STEP 4== of the parking procedure.

        @param files List with filename(s) of the featured picture or set.
        """
        # For set nominations just use the first file
        filename = files[0]

        # Extract voting results
        nom_page_text = self.page.get(get_redirect=True)
        match = VerifiedResultR.search(nom_page_text)
        try:
            ws = match.group(1)
            wo = match.group(2)
            wn = match.group(3)
        except AttributeError:
            error(f"Error - no verified result found in '{self.page.title()}'")
            ask_for_help(
                f"The nomination [[{self.page.title()}]] is closed, "
                "but does not contain a valid verified result. "
                f"{please_fix_hint}"
            )
            return

        # Get the current monthly overview page
        now = datetime.datetime.now(datetime.UTC)
        year = now.year
        month = now.strftime("%B")  # Full local month name, here: English
        monthpage = f"Commons:Featured pictures/chronological/{month} {year}"
        page = pywikibot.Page(G_Site, monthpage)
        try:
            old_text = page.get(get_redirect=True)
        except pywikibot.exceptions.NoPageError:
            old_text = ""

        if old_text:
            # First check if the image is already on the page.
            # This can happen if the process has previously been interrupted.
            if re.search(wikipattern(filename), old_text):
                out(
                    f"Skipping addToCurrentMonth() for '{filename}', "
                    "image is already listed."
                )
                return
            # Find the number of entries in the gallery
            match = re.search(
                r"<gallery\b[^>]*>(\n.*)</gallery>",
                old_text,
                flags=re.DOTALL,
            )
            try:
                # Because of the obligatory NL after '<gallery>' even
                # an empty gallery must yield a count of 1, as we need it.
                count = match.group(1).count("\n")
            except AttributeError:
                error(f"Error - no valid <gallery> element in '{monthpage}'")
                ask_for_help(
                    f"The monthly overview page [[{monthpage}]] is missing "
                    "a <code><nowiki><gallery></nowiki></code> element. "
                    "Please check the page."
                )
                return
        else:
            # The page does not exist yet (new month) or is empty,
            # so create its contents from scratch.
            old_text = (
                "{{FPArchiveChrono}}\n"
                "\n"
                f"== {month} {year} ==\n"
                "<gallery>\n</gallery>"
            )
            count = 1

        # Assemble the new entry and append it to the end of the gallery
        if self.isSet():
            set_name = self.subpageName(keep_prefix=False, keep_number=False)
            title = f"Set: {set_name} ({len(files)} files)"
            message = f"Added set [[{self.page.title()}|{set_name}]]"
        else:
            title = bare_filename(filename)
            message = f"Added [[{filename}]]"
        creator_link = self.creator(link=True)
        uploader_link = self.uploader(filename, link=True)
        nominator_link = self.nominator(link=True)
        if creator_link and creator_link != uploader_link:
            # We omit the creator if the creator is identical to the uploader,
            # but mention uploader and nominator separately even if they are
            # one and the same, to keep the traditional format of the overview
            # as far as possible in order to simplify statistical analysis.
            creator_hint = f"created by {creator_link}, "
        else:
            creator_hint = ""
        new_text = old_text.replace(
            "</gallery>",
            f"{filename}|[[{self.page.title()}|{count}]] '''{title}'''<br> "
            f"{creator_hint}"
            f"uploaded by {uploader_link}, "
            f"nominated by {nominator_link},<br> "
            f"{{{{s|{ws}}}}}, {{{{o|{wo}}}}}, {{{{n|{wn}}}}}\n"
            "</gallery>",
            1,
        )
        commit(old_text, new_text, page, message)

    def notifyNominator(self, files):
        """
        Add a FP promotion template to the nominator's talk page.
        Should only be called on closed and verified candidates.

        This is ==STEP 5== of the parking procedure.

        @param files List with filename(s) of the featured picture or set.
        """
        # Get and read nominator talk page
        talk_link = "User talk:" + self.nominator(link=False)
        talk_page = pywikibot.Page(G_Site, talk_link)
        ignoring = "but ignoring since it's just the nominator notification."
        try:
            old_text = talk_page.get(get_redirect=False)
        except pywikibot.exceptions.NoPageError:
            # Undefined user talk pages are uncommon because every new user
            # is welcomed by an automatic message.  So better stop here.
            warn(f"The user talk page '{talk_link}' is undefined, {ignoring}")
            return
        except pywikibot.exceptions.IsRedirectPageError:
            # Try to resolve the redirect
            try:
                talk_page = talk_page.getRedirectTarget()
                old_text = talk_page.get(get_redirect=False)
            except pywikibot.exceptions.PageRelatedError:
                # Circular, nested or invalid redirect etc.
                warn(
                    f"The user talk page '{talk_link}' was moved "
                    f"and the redirect is invalid, {ignoring}"
                )
                return
            new_talk_link = talk_page.title()
            out(f"User talk page redirect: '{talk_link}' -> '{new_talk_link}'")
            talk_link = new_talk_link  # Update the talk page name.

        subpage_name = self.subpageName(keep_prefix=True, keep_number=True)
        if self.isSet():
            # Notifications for set nominations add a gallery to the talk page
            # and use a special template with an appropriate message.
            # Since August 2025 we use an improved version of the template.
            nomination_link = self.page.title()
            set_title = self.subpageName(keep_prefix=False, keep_number=False)
            template = (
                f"{{{{FPpromotionSet2|{set_title}|subpage={subpage_name}}}}}"
            )
            # Check if there already is a promotion template on the talk page.
            # This can happen if the process has previously been interrupted.
            if re.search(wikipattern(template), old_text):
                out(
                    f"Skipping notifyNominator() for set '{set_title}', "
                    f"promotion template is already present at '{talk_link}'."
                )
                return
            entries = "\n".join(
                f"  {filename}|{bare_filename(filename)}" for filename in files
            )
            new_text = (
                f"{old_text.rstrip()}\n"
                "\n"
                "== Set Promoted to FP ==\n"
                '<gallery mode="packed-hover" heights="80px">\n'
                f"{entries}\n"
                "</gallery>\n"
                f"{template} /~~~~"
            )
            message = f"FP promotion of set [[{nomination_link}|{set_title}]]"

        else:
            # Single FP nomination
            filename = files[0]
            template = f"{{{{FPpromotion|{filename}|subpage={subpage_name}}}}}"
            # Check if there already is a promotion template on the talk page.
            # This can happen if the process has previously been interrupted.
            if re.search(wikipattern(template), old_text):
                out(
                    f"Skipping notifyNominator() for '{filename}', "
                    f"promotion template is already present at '{talk_link}'."
                )
                return
            new_text = (
                f"{old_text.rstrip()}\n"
                "\n"
                "== FP Promotion ==\n"
                f"{template} /~~~~"
            )
            message = f"FP promotion of [[{filename}]]"

        # Commit the new text
        try:
            commit(old_text, new_text, talk_page, message)
        except pywikibot.exceptions.LockedPageError:
            warn(f"The user talk page '{talk_link}' is locked, {ignoring}")

    def notifyUploaderAndCreator(self, files):
        """
        Add a FP promotion template to the talk page of the uploader and
        (optionally) of the original creator.  (Sometimes the creator
        is different from the uploader, e.g. when we promote a variant
        of an image which has been retouched by another user.
        In this case we notify also the original creator, if possible.)
        Should only be called on closed and verified candidates.

        This is ==STEP 6== of the parking procedure.

        To understand this method and how it differs from notifyNominator(),
        please keep in mind that all files in a set nomination have the same
        nominator, but they may have been uploaded by different users.
        That's very unusual and discouraged by the current FPC rules,
        but the bot stills supports that special case.  Therefore this method
        handles the files one by one, unlike notifyNominator().
        (Theoretically we would also need to support different creators,
        but at least for now we extract the creator name from the nomination,
        therefore we can handle just a single creator per nomination.)

        @param files List with filename(s) of the featured picture or set.
        """
        ignored_pages = set()
        redirects = {}  # Mapping: old page name -> new page name
        nominator_name = self.nominator(link=False)
        creator_name = self.creator(link=False)
        for filename in files:
            # Check if nominator, uploader and creator are the same user,
            # to avoid adding two templates to the same talk page
            uploader_name = self.uploader(filename, link=False)
            if uploader_name != nominator_name:
                self._notifyUploaderOrCreator(
                    filename, True, uploader_name, ignored_pages, redirects
                )
            else:
                out(
                    f"Skipping uploader notification for '{filename}', "
                    "uploader is identical to nominator."
                )
            if (
                creator_name
                and creator_name != nominator_name
                and creator_name != uploader_name
            ):
                self._notifyUploaderOrCreator(
                    filename, False, creator_name, ignored_pages, redirects
                )
            else:
                out(
                    f"Skipping creator notification for '{filename}', "
                    + (
                        "creator is identical to nominator/uploader."
                        if creator_name else
                        "can't identify the creator."
                    )
                )

    def _notifyUploaderOrCreator(
        self, filename, is_uploader, username, ignored_pages, redirects
    ):
        """Subroutine which implements the uploader/creator notification."""
        if is_uploader:
            role = "uploader"
            tmpl_name = "FPpromotedUploader"
        else:
            role = "creator"
            tmpl_name = "FPpromotedCreator"
        ignoring = f"but ignoring since it's just the {role} notification."

        # Find and read the user talk page
        talk_link = "User talk:" + username
        if talk_link in ignored_pages:
            # Don't load or report undefined or locked talk pages twice
            return
        talk_link = redirects.get(talk_link, talk_link)
        talk_page = pywikibot.Page(G_Site, talk_link)
        try:
            old_text = talk_page.get(get_redirect=False)
        except pywikibot.exceptions.NoPageError:
            # Undefined user talk pages are uncommon because every new user
            # is welcomed by an automatic message.  So better stop here.
            warn(f"The user talk page '{talk_link}' is undefined, {ignoring}")
            ignored_pages.add(talk_link)
            return
        except pywikibot.exceptions.IsRedirectPageError:
            # Try to resolve the redirect
            try:
                talk_page = talk_page.getRedirectTarget()
                old_text = talk_page.get(get_redirect=False)
            except pywikibot.exceptions.PageRelatedError:
                # Circular, nested or invalid redirect etc.
                warn(
                    f"The user talk page '{talk_link}' was moved "
                    f"and the redirect is invalid, {ignoring}"
                )
                ignored_pages.add(talk_link)
                return
            # Record redirect to avoid repeated resolving, update variable
            new_talk_link = talk_page.title()
            redirects[talk_link] = new_talk_link
            out(f"User talk page redirect: '{talk_link}' -> '{new_talk_link}'")
            talk_link = new_talk_link

        # Assemble the template
        subpage_name = self.subpageName(keep_prefix=True, keep_number=True)
        template = f"{{{{{tmpl_name}|{filename}|subpage={subpage_name}}}}}"

        # Check if there already is a promotion template for the file
        # on the user talk page.  If yes, we skip that file.
        if re.search(wikipattern(template), old_text):
            out(
                f"Skipping {role} notification for '{filename}', "
                f"promotion template is already present at '{talk_link}'."
            )
            return

        # Update the description and commit the new text
        new_text = (
            f"{old_text.rstrip()}\n"
            "\n"
            "== FP Promotion ==\n"
            f"{template} /~~~~"
        )
        message = f"FP promotion of [[{filename}]]"
        try:
            commit(old_text, new_text, talk_page, message)
        except pywikibot.exceptions.LockedPageError:
            warn(f"The user talk page '{talk_link}' is locked, {ignoring}")
            ignored_pages.add(talk_link)

    def moveToLog(self, reason=None):
        """
        Remove this candidate from the list of current candidates
        and add it to the log for the current month.
        Should only be called on closed and verified candidates.

        This is ==STEP 7== of the parking procedure.
        """
        subpage_name = self.page.title()
        why = f" ({reason})" if reason else ""

        # Find and read the log page for this month
        # (if it does not exist yet it is just created from scratch)
        now = datetime.datetime.now(datetime.UTC)
        year = now.year
        month = now.strftime("%B")  # Full local month name, here: English
        log_link = f"Commons:Featured picture candidates/Log/{month} {year}"
        log_page = pywikibot.Page(G_Site, log_link)
        try:
            old_log_text = log_page.get(get_redirect=True)
        except pywikibot.exceptions.NoPageError:
            old_log_text = ""

        # Append nomination to the log page
        if re.search(wikipattern(subpage_name), old_log_text):
            # This can happen if the process has previously been interrupted.
            out(
                f"Skipping add in moveToLog() for '{subpage_name}', "
                "candidate is already in the log."
            )
        else:
            new_log_text = old_log_text + "\n{{" + subpage_name + "}}"
            commit(
                old_log_text,
                new_log_text,
                log_page,
                f"Added [[{subpage_name}]]{why}",
            )

        # Remove nomination from the list of current nominations
        candidates_list_page = pywikibot.Page(G_Site, self._listPageName)
        old_cand_text = candidates_list_page.get(get_redirect=True)
        pattern = r" *\{\{\s*" + wikipattern(subpage_name) + r"\s*\}\} *\n?"
        new_cand_text = re.sub(pattern, "", old_cand_text, count=1)
        if old_cand_text == new_cand_text:
            # This can happen if the process has previously been interrupted.
            out(
                f"Skipping remove in moveToLog() for '{subpage_name}', "
                "candidate not found in list."
            )
        else:
            commit(
                old_cand_text,
                new_cand_text,
                candidates_list_page,
                f"Removed [[{subpage_name}]]{why}",
            )

    def park(self):
        """
        Check that the candidate has exactly one valid verified result,
        that the image file(s) exist and that there are no other obstacles.
        If yes, park the candidate -- i.e., if the nomination was successful,
        promote the new FP(s) or delist the former FP respectively;
        else, if it has failed, just archive the nomination.
        """
        subpage_name = self.page.title()
        cut_title = self.cutTitle()

        # Check that the nomination subpage actually exists
        if not self.page.exists():
            error(f"{cut_title}: (Error: no such page?!)")
            ask_for_help(
                list_includes_missing_subpage.format(
                    list=self._listPageName, subpage=subpage_name
                )
            )
            return

        # First look for verified results
        # (leaving out stricken or commented results which have been corrected)
        text = self.page.get(get_redirect=True)
        redacted_text = filter_content(text)
        results = self._VerifiedR.findall(redacted_text)
        # Stop if there is not exactly one valid verified result
        if not results:
            out(f"{cut_title}: (ignoring, no verified results)")
            return
        if len(results) > 1:
            error(f"{cut_title}: (Error: several verified results?)")
            ask_for_help(
                f"The nomination [[{subpage_name}]] seems to contain "
                "more than one verified result. "
                "Please remove (or cross out) all but one of the results."
            )
            return
        if self.isWithdrawn():
            out(f"{cut_title}: (ignoring, was withdrawn)")
            return
        if self.isFPX():
            out(f"{cut_title}: (ignoring, was FPXed)")
            return

        # Check that the image page(s) exist, if not ignore this candidate
        if self.isSet():
            set_files = self.setFiles()
            if not set_files:
                error(f"{cut_title}: (Error: found no images in set)")
                ask_for_help(
                    f"The set nomination [[{subpage_name}]] seems to contain "
                    "no images. Perhaps the formatting is damaged. "
                    f"{please_fix_hint}"
                )
                return
            for filename in set_files:
                if not pywikibot.Page(G_Site, filename).exists():
                    error(
                        f"{cut_title}: (Error: can't find "
                        f"set image '{filename}')"
                    )
                    ask_for_help(
                        f"The set nomination [[{subpage_name}]] lists the "
                        f"file [[:{filename}]], but that file does not exist. "
                        f"Perhaps the file has been renamed. {please_fix_hint}"
                    )
                    return
        elif not pywikibot.Page(G_Site, self.fileName()).exists():
            error(f"{cut_title}: (Error: can't find image page)")
            ask_for_help(
                f"The nomination [[{subpage_name}]] is about the image "
                f"[[:{self.fileName()}]], but that file does not exist. "
                f"Perhaps the file has been renamed. {please_fix_hint}"
            )
            return

        # We should now have a candidate with verified result that we can park
        verified_result = results[0]
        success = verified_result[3]
        if success in {"yes", "no"}:
            # If the keyword has not yet been added to the heading, add it now
            new_text = self.fixHeading(text, success)
            if new_text != text:
                commit(text, new_text, self.page, "Fixed header")
            # Park the candidate
            if success == "yes":
                self.handlePassedCandidate(verified_result)
            else:
                self.moveToLog(self._conString)
        else:
            error(
                f"{cut_title}: (Error: invalid verified "
                f"success status '{success}')"
            )
            ask_for_help(
                f"The verified success status <code>{success}</code> "
                f"in the results template of [[{subpage_name}]] "
                f"is invalid. {please_fix_hint}"
            )

    @abc.abstractmethod
    def handlePassedCandidate(self, results):
        """
        Handle the parking procedure for a passed candidate.
        Must be implemented by the subclasses.
        """
        pass


class FPCandidate(Candidate):
    """A candidate up for promotion."""

    def __init__(self, page, listName):
        """
        The initializer calls the superclass initializer in order to set
        instance variables to the appropriate values for this class.

        @param page A pywikibot.Page object for the nomination subpage.
        """
        super().__init__(
            page,
            listName,
            SupportR,
            OpposeR,
            NeutralR,
            "featured",
            "not featured",
            ReviewedTemplateR,
            CountedTemplateR,
            VerifiedResultR,
        )

    def getResultString(self):
        """
        Returns the results template to be added when closing a nomination.
        Implementation for FP candidates.
        """
        gallery = self.findGalleryOfFile()
        if self.imageCount() > 1:
            return (
                "{{FPC-results-unreviewed"
                "|support=X|oppose=X|neutral=X"
                f"|featured=X|gallery={gallery}|alternative="
                "|sig=<small>'''Note: this candidate has several alternatives. "
                "Thus, if featured, the code <code>alternative=</code> "
                "in this template needs to be followed by the filename "
                "of the chosen alternative.'''</small> "
                "/~~~~}}"
            )
        else:
            return (
                "{{FPC-results-unreviewed"
                f"|support={self._pro}|oppose={self._con}|neutral={self._neu}"
                f"|featured={'yes' if self.isPassed() else 'no'}"
                f"|gallery={gallery}"
                "|sig=~~~~}}"
            )

    def getCloseCommitComment(self):
        if self.imageCount() > 1:
            return "Closing for review - contains alternatives, needs manual count"
        else:
            return (
                "Closing for review (%d support, %d oppose, %d neutral, featured=%s)"
                % (self._pro, self._con, self._neu, "yes" if self.isPassed() else "no")
            )

    def handlePassedCandidate(self, results):
        """
        Promotes a new featured picture (or set of featured pictures):
        adds it to the appropriate gallery page, to the monthly overview
        and to the landing-page list of recent FPs,
        inserts the {{Assessments}} template into the description page(s),
        notifies nominator and uploader, etc.
        """
        subpage_name = self.page.title()
        cut_title = self.cutTitle()

        # Some methods need the full gallery link with section anchor,
        # others only the gallery page name or even just the basic gallery.
        full_gallery_link = results[4].strip()
        gallery_page = re.sub(r"#.*", "", full_gallery_link).rstrip()
        if not gallery_page:
            error(f"{cut_title}: (ignoring, gallery not defined)")
            ask_for_help(
                f"The gallery link in the nomination [[{subpage_name}]] "
                f"is empty or broken. {please_fix_hint}"
            )
            return
        basic_gallery = re.search(r"^(.*?)(?:/|$)", gallery_page).group(1)

        # If there is more than one image, search for the selected alternative
        if self.imageCount() > 1:
            if len(results) > 5 and results[5]:
                alternative = results[5]
                if not pywikibot.Page(G_Site, alternative).exists():
                    error(
                        f"{cut_title}: (ignoring, specified alternative "
                        f"'{alternative}' not found)"
                    )
                    ask_for_help(
                        f"Cannot find the alternative [[:{alternative}]] "
                        f"specified by the nomination [[{subpage_name}]]. "
                        f"{please_fix_hint}"
                    )
                    return
                self._alternative = alternative
            else:
                error(f"{cut_title}: (ignoring, alternative not set)")
                ask_for_help(
                    f"The nomination [[{subpage_name}]] contains several "
                    "images, but does not specify the selected alternative. "
                    f"{please_fix_hint}"
                )
                return

        # Promote the new featured picture(s)
        files = self.setFiles() if self.isSet() else [self.fileName()]
        if not files:
            error(f"{cut_title}: (ignoring, no file(s) found)")
            ask_for_help(
                "Cannot find the featured file(s) in the nomination "
                f"[[{subpage_name}]]. {please_fix_hint}"
            )
            return
        self.addToFeaturedList(basic_gallery, files)
        self.addToGalleryPage(full_gallery_link, files)
        self.addAssessments(files)
        self.addToCurrentMonth(files)
        self.notifyNominator(files)
        self.notifyUploaderAndCreator(files)
        self.moveToLog(self._proString)


class DelistCandidate(Candidate):
    """A delisting candidate."""

    def __init__(self, page, listName):
        """
        The initializer calls the superclass initializer in order to set
        instance variables to the appropriate values for this class.

        @param page A pywikibot.Page object for the nomination subpage.
        """
        super().__init__(
            page,
            listName,
            DelistR,
            KeepR,
            NeutralR,
            "delisted",
            "not delisted",
            DelistReviewedTemplateR,
            DelistCountedTemplateR,
            VerifiedDelistResultR,
        )

    def getResultString(self):
        """
        Returns the results template to be added when closing a nomination.
        Implementation for delisting candidates.
        """
        if self.imageCount() != 1 or self.isSet():
            # A delist-and-replace or a set delisting nomination
            return (
                "{{FPC-delist-results-unreviewed"
                "|delist=X|keep=X|neutral=X|delisted=X"
                "|sig=<small>'''Note: This appears to be a delist-and-replace "
                "or set delisting nomination (or something else special). "
                "It must therefore be counted and processed manually.'''"
                "</small> ~~~~}}"
            )
        # A simple delisting nomination
        return (
            "{{FPC-delist-results-unreviewed"
            f"|delist={self._pro}|keep={self._con}|neutral={self._neu}"
            f"|delisted={'yes' if self.isPassed() else 'no'}"
            "|sig=~~~~}}"
        )

    def getCloseCommitComment(self):
        """Implementation for delisting candidates."""
        if self.imageCount() != 1 or self.isSet():
            # A delist-and-replace or a set delisting nomination
            return (
                "Closing for review - looks like a delist-and-replace "
                "or set delisting nomination, needs manual count"
            )
        # A simple delisting nomination
        return (
            "Closing for review "
            f"({self._pro} delist, {self._con} keep, {self._neu} neutral, "
            f"delisted={'yes' if self.isPassed() else 'no'})"
        )

    def handlePassedCandidate(self, results):
        """
        Handle the parking procedure for a passed delisting candidate:
        remove the image from FP gallery pages, mark it as delisted
        in the chronological archives, update the {{Assessents}} template
        and remove FP categories from the image description page, etc.
        """
        if self.imageCount() != 1 or self.isSet():
            # Support for delist-and-replace nominations and set delisting
            # is yet to be implemented.  Therefore ask for help and abort.
            ask_for_help(
                "The bot is not yet able to handle delist-and-replace "
                "nominations or set delisting nominations. "
                "Therefore, please take care of the images "
                f"from the nomination [[{self.page.title()}]] "
                "and remove or replace them manually."
            )
            return
        self.removeFromGalleryPages(results)
        self.removeAssessments()
        self.moveToLog(self._proString)

    def removeFromGalleryPages(self, results):
        """
        Remove a delisted FP from the FP gallery pages and mark its entry
        in the chronological archive as delisted.
        """
        # We skip checking the FP landing page with the newest FPs;
        # the chance that the image is still there is very small,
        # and even then that page will soon be updated anyway.
        nomination_link = self.page.title()
        filename = self.fileName()
        fn_pattern = wikipattern(filename.replace("File:", ""))
        file_page = pywikibot.FilePage(G_Site, title=filename)
        if not file_page.exists():
            error(f"Error - image '{filename}' not found.")
            return
        using_pages = file_page.using_pages(
            namespaces=["Commons"], filterredir=False
        )
        for page in using_pages:
            page_name = page.title()
            if not page_name.startswith("Commons:Featured pictures/"):
                # Any other page -- don't remove the image here, of course.
                continue
            try:
                old_text = page.get(get_redirect=False)
            except pywikibot.exceptions.PageRelatedError as exc:
                error(f"Error - could not read {page_name}: {exc}")
                continue
            if page_name.startswith("Commons:Featured pictures/chronological"):
                # Chronological archive page: mark the image as delisted
                out(f"Adding delist note to '{page_name}'...")
                if match := re.search(
                    r"((?:[Ff]ile|[Ii]mage):%s.*)\n" % fn_pattern, old_text
                ):
                    if re.search(r"[Dd]elisted", match.group(1)):
                        out(f"Already marked as delisted on '{page_name}'.")
                        continue
                    now = datetime.datetime.now(datetime.UTC)
                    entry = (
                        # Entries often end with trailing spaces, strip them
                        f"{match.group(1).rstrip()} "
                        f"'''[[{nomination_link}|Delisted]] {now:%Y-%m-%d} "
                        f"({results[1]}\u2013{results[0]})'''"
                    )
                    new_text = (
                        old_text[:match.start(1)]
                        + entry
                        + old_text[match.end(1):]
                    )
                else:
                    # Did not find the image.  That's OK e.g. for the overview
                    # pages which include the archives of each half year,
                    # therefore don't print an error here.
                    out(
                        f"Did not find '{filename}' on '{page_name}'; "
                        "that's OK if this is just a transclusion page etc."
                    )
                    continue
                summary = f"Delisted [[{filename}]] per [[{nomination_link}]]"
            else:
                # FP gallery page: remove the entry (line) with the image
                out(f"Removing delisted image from '{page_name}'...")
                new_text, n = re.subn(
                    r"(\[\[)?([Ff]ile|[Ii]mage):%s.*\n" % fn_pattern,
                    "",
                    old_text,
                )
                if n == 0:
                    error(
                        f"Error - could not remove '{filename}' "
                        f"from '{page_name}'."
                    )
                    continue
                summary = f"Removed [[{filename}]] per [[{nomination_link}]]"
            if new_text != old_text:
                try:
                    commit(old_text, new_text, page, summary)
                except pywikibot.exceptions.LockedPageError:
                    error(f"Error - page '{page_name}' is locked.")
            else:
                error(
                    f"Error - removing/delisting '{filename}' "
                    f"did not work on '{page_name}'."
                )

    def removeAssessments(self):
        """Remove FP status from the image description page."""
        # Get and read image description page
        filename = self.fileName()
        image_page = pywikibot.Page(G_Site, filename)
        try:
            old_text = image_page.get(get_redirect=False)
        except pywikibot.exceptions.PageRelatedError as exc:
            error(f"Error - can't read '{filename}': {exc}")
            return
        subpage_name = self.subpageName(keep_prefix=False, keep_number=True)

        # Update the {{Assessments}} template
        # We have to replace 'featured=1' by '=2' and to update the name
        # of the nomination subpage in the 'com-nom'/'subpage' parameter
        # to make sure that the link in the template correctly points
        # to the delist nomination (and not to the original nomination).
        # The replacement strings use the '\g<1>' notation because r'\12'
        # would be misinterpreted as backreference to (non-existent) group 12,
        # and the name of the nomination subpage could start with a figure.
        if match := AssessmentsR.search(old_text):
            params = match.group(1)
            params, n = re.subn(
                r"(\|\s*(?:com|featured)\s*=\s*)\d",
                r"\g<1>2",
                params,
                count=1,
            )
            if n == 0:
                params += "|featured=2"
            params, n = re.subn(
                r"(\|\s*)(?:com-nom|subpage)(\s*=\s*)[^{}|\n]+",
                r"\g<1>com-nom\g<2>" + subpage_name,
                params,
                count=1,
            )
            if n == 0:
                params += f"|com-nom={subpage_name}"
            new_text = (
                old_text[:match.start(1)]
                + params
                + old_text[match.end(1):]
            )
        else:
            error(f"Error - no {{{{Assessments}}}} found on '{filename}'.")
            return

        # Remove 'Featured pictures of/from/by ...' categories.
        # We must not touch project-specific categories like
        # 'Featured pictures on Wikipedia, <language>'.
        new_text = re.sub(
            r"\[\[[Cc]ategory: *Featured pictures (?!on ).+?\]\] *\n?",
            "",
            new_text,
        )
        new_text = re.sub(
            r"\[\[[Cc]ategory: *Featured (?:[a-z -]+)?"
            r"photo(?:graphs|graphy|s).*?\]\] *\n?",
            "",
            new_text,
        )
        new_text = re.sub(
            r"\[\[[Cc]ategory: *Featured (?:diagrams|maps).*?\]\] *\n?",
            "",
            new_text,
        )

        # Commit the text of the page if it has changed
        if new_text != old_text:
            summary = f"Delisted per [[{self.page.title()}]]"
            try:
                commit(old_text, new_text, image_page, summary)
            except pywikibot.exceptions.LockedPageError:
                error(f"Error - '{filename}' is locked.")
        else:
            error(
                f"Error - removing FP status from '{filename}' "
                f"did not work."
            )


def wikipattern(s):
    """
    Prepares a filename, page name etc. so that it can be used in a regex
    and that spaces and underscores are handled as interchangeable,
    as usual in MediaWiki filenames, page names etc.
    """
    return re.sub(r"(?:\\ |_)", r"[ _]", re.escape(s))


# If this assertion ever fails, re.escape() handles spaces differently now,
# so please update the regex in the function above.
assert re.escape(" ") == r"\ "


def out(text, newline=True, date=False, heading=False):
    """Output information or status messages to the console or log."""
    if heading:
        text = f"<<lightblue>>{text}<<default>>"
    dstr = (
        f"{datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M:%S')}: "
        if date and not G_LogNoTime
        else ""
    )
    pywikibot.stdout(f"{dstr}{text}", newline=newline)


def warn(text, newline=True):
    """
    Output a warning to the console or log.  We use this if something
    does not work as expected, but it's probably not necessary to take action.

    TODO: Consider to use pywikibot.warning() instead of pywikibot.stdout(),
    but first clarify whether any log settings need to be be changed
    on the server then.
    """
    pywikibot.stdout(f"<<lightyellow>>{text}<<default>>", newline=newline)


def error(text, newline=True):
    """
    Output an error message to the console or log.  We use this
    if something does not work and it's probably necessary to take action,
    e.g. to fix the wikitext of a nomination or gallery page, etc.,
    or to improve the code of the bot program.

    TODO: Consider to use pywikibot.error() instead of pywikibot.stdout(),
    but first clarify whether any log settings need to be be changed
    on the server then.
    """
    pywikibot.stdout(f"<<lightred>>{text}<<default>>", newline=newline)


def findCandidates(list_page_name, delist):
    """
    Returns a list with candidate objects for all nomination subpages,
    either from the page with the current candidates or from a log page
    with closed nominations.
    The list retains the original order of entries and omits damaged links.
    If we find redirects to renamed nomination subpages, they are resolved
    (so the returned candidate objects point to the actual nominations)
    and the page with the list of candidates is updated.

    @param list_page_name The name either of the page with the list of
        current candidates or of the log page that we want to check.
    @param delist         Specify True to get only delist nominations,
        False to get only FP nominations.
    """
    # Extract nomination subpage names
    out(
        f"Extracting {'delist' if delist else 'FP'} candidates, "
        "checking for redirects..."
    )
    page = pywikibot.Page(G_Site, list_page_name)
    try:
        old_text = page.get(get_redirect=False)
    except pywikibot.exceptions.PageRelatedError as exc:
        error(f"Error - can't read candidate list '{list_page_name}': {exc}.")
        ask_for_help(
            f"The bot cannot read the candidate list [[{list_page_name}]]: "
            f"{exc} This is a serious problem, please check the page."
        )
        return []
    without_comments = re.sub(r"<!--.+?-->", "", old_text, flags=re.DOTALL)
    subpage_entries = re.findall(
        r"(\{\{ *(Commons:Featured[ _]picture[ _]candidates */[^\n}]+?)\}\})",
        without_comments,
    )
    if not subpage_entries:
        error(f"Error - no candidates found in '{list_page_name}'.")
        ask_for_help(
            f"The candidate list [[{list_page_name}]] does not appear "
            "to contain a single nomination. That is a bit peculiar. "
            "Please check whether this is correct or not."
        )
        return []
    candidate_class = DelistCandidate if delist else FPCandidate
    match_pattern = G_MatchPattern.lower()
    candidates = []
    redirects = []

    for full_entry, subpage_name in subpage_entries:
        # Skip nominations which are not of the expected type
        if bool(re.search(r"/ *[Rr]emoval */", subpage_name)) != delist:
            continue
        # Skip nominations which do not match the '-match' argument
        if match_pattern:
            comparison_name = PrefixR.sub("", subpage_name).lower()
            if match_pattern not in comparison_name:
                continue
        subpage = pywikibot.Page(G_Site, subpage_name)
        # Check if nomination exists (filter out damaged links)
        if not subpage.exists():
            error(f"Error - nomination '{subpage_name}' not found, ignoring.")
            ask_for_help(
                list_includes_missing_subpage.format(
                    list=list_page_name, subpage=subpage_name
                )
            )
            continue
        # Check for redirects and and resolve them
        if subpage.isRedirectPage():
            try:
                subpage = subpage.getRedirectTarget()
            except pywikibot.exceptions.PageRelatedError:
                # Circular or invalid redirect etc.
                error(
                    "Error - invalid nomination redirect page "
                    f"'{subpage_name}', ignoring."
                )
                ask_for_help(
                    f"The nomination subpage [[{subpage_name}]] "
                    f"contains an invalid redirect. {please_fix_hint}"
                )
                continue
            new_name = subpage.title()
            out(f"Nomination '{subpage_name}' has been renamed to '{new_name}'")
            redirects.append((full_entry, f'{{{{{new_name}}}}}'))
        # OK, seems the nomination is fine -- append candidate object
        candidates.append(candidate_class(subpage, list_page_name))

    # If we have found any redirects, update the candidates page
    if redirects:
        new_text = old_text
        for full_entry, new_entry in redirects:
            new_text = new_text.replace(full_entry, new_entry, 1)
        message = (
            f"Resolved {len(redirects)} redirect(s) to renamed nomination(s)"
        )
        commit(old_text, new_text, page, message)
    return candidates


def checkCandidates(check, list_page_name, delist, descending=True):
    """
    Calls a function on each candidate found on the specified page.

    @param check      A method of the Candidate class which should be called
        on each candidate.
    @param list_page_name The name of the page which includes all nominations;
        i.e. either the page with the list of current candidates
        or a log page that we want to check for test purposes.
    @param delist     Specify True to get only delist nominations,
        False to get only FP nominations.
    @param descending Specify True if the page puts the newest entries first,
        False if it runs from the oldest to the newest entry.
        So we can always handle the candidates in chronological order.
    """
    if not G_Site.logged_in():
        G_Site.login()

    # Find all current candidates
    candidates = findCandidates(list_page_name, delist)
    if not candidates:
        out(
            f"Found no {'delist' if delist else 'FP'} candidates"
            f"{' matching the -match argument' if G_MatchPattern else ''}."
        )
        return
    if descending:
        candidates.reverse()

    # Handle each candidate with the specified method
    total = len(candidates)
    for i, candidate in enumerate(candidates, start=1):
        if not G_Threads:
            out(f"({i:03d}/{total:03d}) ", newline=False, date=True)

        try:
            if G_Threads:
                while threading.active_count() >= config.max_external_links:
                    time.sleep(0.1)
                thread = ThreadCheckCandidate(candidate, check)
                thread.start()
            else:
                check(candidate)
        except pywikibot.exceptions.NoPageError as exc:
            error(f"Error - no such page: '{exc}'")
            ask_for_help(f"{exc} Please check this.")
        except pywikibot.exceptions.LockedPageError as exc:
            error(f"Error - page is locked: '{exc}'")
            ask_for_help(f"{exc} Please check this.")
        except Exception as exc:
            # Report exception with stack trace on the FPC talk page
            stack_trace = traceback.format_exc().rstrip()
            stack_trace = re.sub(  # Abbreviate file paths to filenames
                r'(File ").+?/([^/\n]+\.py")', r"\1\2", stack_trace
            )
            try:
                subpage_link = f"[[{candidate.page.title()}]]"
            except Exception:
                subpage_link = f"the invalid nomination no. {i}"
            ask_for_help(
                f"The bot has stopped at {subpage_link} "
                "because of an uncaught exception:\n"
                f"<pre>{stack_trace}</pre>\n"
                "Developers, please look into this."
            )
            # Raise the exception again to enable normal error logging
            raise exc

        if G_Abort:
            break


def filter_content(text):
    """
    Filter out all content that should not be taken into account
    when counting votes etc.

    Currently this includes:
    * the <s> tag for striking out votes
    * the <nowiki> tag which is just for displaying syntax
    * image notes
    * collapse boxes
    * comments
    """
    text = strip_tag(text, "[Ss]")
    text = strip_tag(text, "[Nn]owiki")
    text = strip_tag(text, "[Ss]trike")
    text = strip_tag(text, "[Dd]el")
    text = re.sub(
        r"\{\{\s*[Ii]mageNote\s*\|.*?\}\}.*?\{\{\s*[iI]mageNoteEnd.*?\}\}",
        "",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"\{\{\s*[Cc](?:ollapse[ _]top|ot)\s*\|.*?\}\}.*?"
        r"\{\{\s*[Cc](?:ollapse[ _]bottom|ob)\s*\}\}",
        "",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return text


def strip_tag(text, tag):
    """Will simply take a tag and remove a specified tag."""
    return re.sub(r"(?s)<%s>.*?</%s>" % (tag, tag), "", text)


def bare_filename(filename):
    """
    Returns the bare filename without 'File:' prefix and w/o file extension.
    Useful for labels, image captions, etc.
    """
    return re.sub(
        r"^(?:[Ff]ile|[Ii]mage):(.+?)\.\w{2,4}$",
        r"\1",
        filename,
        count=1,
    ).strip()


def user_page_link(username):
    """Returns a link to the user page of the user."""
    return f"[[User:{username}|{username}]]"


def oldest_revision_user(page):
    """
    Returns the name of the user who has created the oldest (first) revision
    of a page on Wikimedia Commons; on errors just returns ''.

    @param page A pywikibot.Page object.
    """
    try:
        return page.oldest_revision.user.strip()
    except (pywikibot.exceptions.PageRelatedError, AttributeError):
        return ""


def findEndOfTemplate(text, template):
    """
    As regexps can't properly deal with nested parantheses.
    this function will manually scan for where a template ends
    such that we can insert new text after it.
    Will return the position or 0 if not found.
    """
    m = re.search(r"{{\s*%s" % template, text)
    if not m:
        return 0

    lvl = 0
    cp = m.start() + 2

    while cp < len(text):
        ns = text.find("{{", cp)
        ne = text.find("}}", cp)

        # If we see no end tag, we give up
        if ne == -1:
            return 0

        # Handle case when there are no more start tags
        if ns == -1:
            if not lvl:
                return ne + 2
            else:
                lvl -= 1
                cp = ne + 2

        elif not lvl and ne < ns:
            return ne + 2
        elif ne < ns:
            lvl -= 1
            cp = ne + 2
        else:
            lvl += 1
            cp = ns + 2
    # Apparently we never found it
    return 0


def ask_for_help(message):
    """
    Adds a short notice to the FPC talk page, asking for help with a problem.
    This is useful if the problem is very probably caused by a broken link,
    a wikitext syntax error, etc. on a Commons page, i.e. issues a normal
    human editor can correct easily.

    @param message A concise description of the problem in one or two
    short, but complete sentences; normally they should end with a request
    to change this or that in order to help the bot.
    """
    talk_page_name = "Commons talk:Featured picture candidates"
    talk_page = pywikibot.Page(G_Site, talk_page_name)
    try:
        old_text = talk_page.get()
    except pywikibot.exceptions.PageRelatedError:
        error(f"Error - could not read FPC talk page '{talk_page_name}'.")
    if message in old_text:
        return  # Don't post the same message twice.
    new_text = old_text.rstrip() + (
        "\n\n== FPCBot asking for help ==\n"
        "[[File:Robot icon.svg|64px|left|link=User:FPCBot]]\n"
        f"{message} Thank you! / ~~~~"
    )
    commit(old_text, new_text, talk_page, "Added request for help")


def commit(old_text, new_text, page, comment):
    """
    This will commit new_text to the page
    and unless running in automatic mode it
    will show you the diff and ask you to accept it.

    @param old_text Used to show the diff
    @param new_text Text to be submitted as the new page
    @param page Page to submit the new text to
    @param comment The edit comment
    """

    out("\n About to commit changes to: '%s'" % page.title())

    # Show the diff
    lines_of_context = 0 if (G_Auto and not G_Dry) else 3
    pywikibot.showDiff(
        old_text,
        new_text,
        context=lines_of_context,
    )

    if G_Dry:
        choice = "n"
    elif G_Auto:
        choice = "y"
    else:
        choice = pywikibot.bot.input_choice(
            "Do you want to accept these changes to '%s' with comment '%s' ?"
            % (page.title(), comment),
            [("yes", "y"), ("no", "n"), ("quit", "q")],
            automatic_quit=False,
        )

    if choice == "y":
        page.put(new_text, summary=comment, watch=None, minor=False)
    elif choice == "q":
        out("Aborting.")
        sys.exit(0)
    else:
        out("Changes to '%s' ignored" % page.title())


# Data and regexps used by the bot

# List of valid templates
# They are taken from the page Commons:Polling_templates and some common redirects
support_templates = (
    "[Ss]upport",
    "[Pp]ro",
    "[Ss]im",
    "[Tt]ak",
    "[Ss]í",
    "[Pp]RO",
    "[Ss]up",
    "[Yy]es",
    "[Oo]ui",
    "[Kk]yllä",  # First support + redirects
    "падтрымліваю",
    "[Pp]our",
    "[Tt]acaíocht",
    "דעב",
    "[Ww]eak support",
    "[Ww]eak [Ss]",
    "[Ss]amþykkt",
    "支持",
    "찬성",
    "[Ss]for",
    "за",
    "[Ss]tödjer",
    "เห็นด้วย",
    "[Dd]estek",
    "[Aa] favore?",
    "[Ss]trong support",
    "[Ss]Support",
    "Υπέρ",
    "[Ww]Support",
    "[Ss]",
    "[Aa]poio",
)
oppose_templates = (
    "[Oo]",
    "[Oo]ppose",
    "[Kk]ontra",
    "[Nn]ão",
    "[Nn]ie",
    "[Mm]autohe",
    "[Oo]pp",
    "[Nn]ein",
    "[Ee]i",  # First oppose + redirect
    "[Cс]упраць",
    "[Ee]n contra",
    "[Cc]ontre",
    "[Ii] gcoinne",
    "[Dd]íliostaigh",
    "[Dd]iscordo",
    "נגד",
    "á móti",
    "反対",
    "除外",
    "반대",
    "[Mm]ot",
    "против",
    "[Ss]tödjer ej",
    "ไม่เห็นด้วย",
    "[Kk]arsi",
    "FPX contested",
    "[Cc]ontra",
    "[Cc]ontrario",
    "[Ss]trong oppose",
    "[Ww]eak oppose",
    "[Ww]eak [Oo]",
)
neutral_templates = (
    "[Nn]eutral?",
    "[Oo]partisk",
    "[Nn]eutre",
    "[Nn]eutro",
    "[Nn]",
    "נמנע",
    "[Nn]øytral",
    "中立",
    "Нэўтральна",
    "[Tt]arafsız",
    "Воздерживаюсь",
    "[Hh]lutlaus",
    "중립",
    "[Nn]eodrach",
    "เป็นกลาง",
    "[Vv]n",
    "[Nn]eutrale",
)
delist_templates = (
    "[Dd]elist",
    "sdf",
)  # Should the remove templates be valid here ? There seem to be no internationalized delist versions
keep_templates = (
    "[Kk]eep",
    "[Vv]k",
    "[Mm]antener",
    "[Gg]arder",
    "維持",
    "[Bb]ehold",
    "[Mm]anter",
    "[Bb]ehåll",
    "เก็บ",
    "保留",
)


# Shared messages

please_fix_hint = (
    "Please check and fix this so that the bot can process the nomination."
)
please_check_gallery_and_sort_fps = (
    "Please check that gallery page and add the new featured picture(s) "
    "from the nomination to the appropriate gallery page."
)
list_includes_missing_subpage = (
    "The candidate list [[{list}]] includes the nomination [[{subpage}]], "
    "but that page does not exist. Perhaps the page has been renamed "
    "and the list needs to be updated. " + please_fix_hint
)


# Compiled regular expressions

# Used to remove the nomination page prefix and the 'File:'/'Image:' namespace
# or to replace both by the standard 'File:' namespace.
# Removes also any possible crap between the prefix and the namespace
# and faulty spaces between namespace and filename (sometimes users
# accidentally add such spaces when creating nominations).
candPrefix = "Commons:Featured picture candidates/"
PrefixR = re.compile(candPrefix + r".*?([Ff]ile|[Ii]mage): *")

# Looks for results using the old, text-based results format
# which was in use until August 2009.  An example of such a line is:
# '''result:''' 3 support, 2 oppose, 0 neutral => not featured.
PreviousResultR = re.compile(
    r"'''[Rr]esult:'''\s+(\d+)\s+support,\s+(\d+)\s+oppose,\s+(\d+)\s+neutral\s*=>\s*((?:not )?featured)",
    re.MULTILINE,
)

# Looks for verified results using the new, template-based format
VerifiedResultR = re.compile(
    r"""
                              {{\s*FPC-results-reviewed\s*\|        # Template start
                              \s*support\s*=\s*(\d+)\s*\|           # Support votes (1)
                              \s*oppose\s*=\s*(\d+)\s*\|            # Oppose Votes  (2)
                              \s*neutral\s*=\s*(\d+)\s*\|           # Neutral votes (3)
                              \s*featured\s*=\s*(\w+)\s*\|          # Featured, should be yes or no, but is not verified at this point (4)
                              \s*gallery\s*=\s*([^|]*)              # A gallery page if the image was featured (5)
                              (?:\|\s*alternative\s*=\s*([^|]*))?   # For candidate with alternatives this specifies the winning image (6)
                              .*}}                                  # END
                              """,
    re.MULTILINE | re.VERBOSE,
)

VerifiedDelistResultR = re.compile(
    r"{{\s*FPC-delist-results-reviewed\s*\|\s*delist\s*=\s*(\d+)\s*\|\s*keep\s*=\s*(\d+)\s*\|\s*neutral\s*=\s*(\d+)\s*\|\s*delisted\s*=\s*(\w+).*?}}",
    re.MULTILINE,
)

# Matches the entire line including newline so they can be stripped away
CountedTemplateR = re.compile(r"^.*{{\s*FPC-results-unreviewed.*}}.*$\n?", re.MULTILINE)
DelistCountedTemplateR = re.compile(
    r"^.*{{\s*FPC-delist-results-unreviewed.*}}.*$\n?", re.MULTILINE
)
ReviewedTemplateR = re.compile(r"^.*{{\s*FPC-results-reviewed.*}}.*$\n?", re.MULTILINE)
DelistReviewedTemplateR = re.compile(
    r"^.*{{\s*FPC-delist-results-reviewed.*}}.*$\n?", re.MULTILINE
)

# Is whitespace allowed at the end ?
SectionR = re.compile(r"^={1,4}.+={1,4}\s*$", re.MULTILINE)
# Voting templates
SupportR = re.compile(
    r"{{\s*(?:%s)(\|.*)?\s*}}" % "|".join(support_templates), re.MULTILINE
)
OpposeR = re.compile(
    r"{{\s*(?:%s)(\|.*)?\s*}}" % "|".join(oppose_templates), re.MULTILINE
)
NeutralR = re.compile(
    r"{{\s*(?:%s)(\|.*)?\s*}}" % "|".join(neutral_templates), re.MULTILINE
)
DelistR = re.compile(
    r"{{\s*(?:%s)(\|.*)?\s*}}" % "|".join(delist_templates), re.MULTILINE
)
KeepR = re.compile(r"{{\s*(?:%s)(\|.*)?\s*}}" % "|".join(keep_templates), re.MULTILINE)
# Finds if a withdraw template is used
# This template has an optional string which we
# must be able to detect after the pipe symbol
WithdrawnR = re.compile(r"{{\s*(?:[wW]ithdrawn?|[fF]PD)\s*(\|.*)?}}", re.MULTILINE)
# Nomination that contain the fpx template
FpxR = re.compile(r"{{\s*FPX(\|.*)?}}", re.MULTILINE)
# Counts the number of displayed images
ImagesR = re.compile(r"(\[\[((?:[Ff]ile|[Ii]mage):[^|]+).*?\]\])")
# Look for a size specification of the image link
ImagesSizeR = re.compile(r"\|.*?(\d+)\s*px")
# Find if there is a thumb parameter specified
ImagesThumbR = re.compile(r"\|\s*thumb\b")
# Finds the last image link on a page
LastImageR = re.compile(
    r"(?s)(\[\[(?:[Ff]ile|[Ii]mage):[^\n]*\]\])(?!.*\[\[(?:[Ff]ile|[Ii]mage):)"
)
# Finds the {{Assessments}} template on an image description page
# (sometimes people break it into several lines, so use '\s' and re.DOTALL)
AssessmentsR = re.compile(
    r"\{\{\s*[Aa]ssessments\s*(\|.*?)\}\}", flags=re.DOTALL
)
# Search nomination for the username of the original creator
CreatorNameR = re.compile(
    r"\{\{[Ii]nfo\}\}.+?"
    r"[Cc]reated +(?:(?:and|\&) +(?:[a-z]+ +)?uploaded +)?by +"
    r"\[\[[Uu]ser:([^|\]\n]+)[|\]]"
)

# Auto reply yes to all questions
G_Auto = False
# Auto answer no
G_Dry = False
# Use threads
G_Threads = False
# Avoid timestamps in output
G_LogNoTime = False
# Pattern to match
G_MatchPattern = ""
# Flag that will be set to True if CTRL-C was pressed
G_Abort = False
# Pywikibot Site object
G_Site = None


def main(*args):
    """
    This function is the main entry point of the bot program.
    It encapsulates the program's primary behavior --
    parsing and checking command-line arguments, defining global variables,
    selecting the desired tasks and calling the appropriate functions.

    @param *args: If you run this script in the usual way as bot program,
    this function is called without any arguments and uses the CLI arguments.
    However for test purposes etc. one could consider to import the script
    like a module and to call this method from Python code;
    in this case pass strings with the same values as the CLI arguments,
    then the '*args' packs all these values into a single tuple.
    """
    global G_Auto
    global G_Dry
    global G_Threads
    global G_LogNoTime
    global G_MatchPattern
    global G_Site

    # Define local constants and default values
    candidates_page = "Commons:Featured picture candidates/candidate list"
    test_log = "Commons:Featured picture candidates/Log/January 2025"
    delist = False
    fpc = False

    # Acquire CLI arguments, let Pywikibot handle the global arguments
    # (including '-help') and get the rest as a simple list
    override_args = args if args else None
    try:
        local_args = pywikibot.handle_args(args=override_args, do_help=True)
    except ConnectionError:
        error("Error - can't connect to the Commons server, aborting.")
        sys.exit()

    # Pywikibot can create the site object only after handling the arguments
    G_Site = pywikibot.Site()

    # First look for arguments which act as options for all tasks
    task_args = []
    i = 0
    while i < len(local_args):
        arg = local_args[i]
        match arg:
            case "-auto":
                G_Auto = True
            case "-dry":
                G_Dry = True
            case "-threads":
                G_Threads = True
            case "-delist":
                delist = True
            case "-fpc":
                fpc = True
            case "-notime":
                G_LogNoTime = True
            case "-match":
                # So the next argument must be the pattern string
                try:
                    G_MatchPattern = local_args[i + 1]
                except IndexError:
                    error(
                        "Error - '-match' must be followed by a pattern, "
                        "aborting."
                    )
                    sys.exit()
                i += 1  # Skip the pattern argument.
            case _:
                task_args.append(arg)
        i += 1

    # If neither -fpc nor -delist is used we handle all candidates
    if not delist and not fpc:
        delist = True
        fpc = True

    # We can't use the interactive mode with threads
    if G_Threads and (not G_Dry and not G_Auto):
        error("Error - '-threads' must be used with '-dry' or '-auto'.")
        sys.exit()

    # Check task arguments
    if not task_args:
        error(
            "Error - you need to specify at least one task "
            "like '-info', '-close', '-park', etc.; see '-help'."
        )
        sys.exit()
    if invalid_args := set(task_args) - {"-test", "-info", "-close", "-park"}:
        # To present a helpful error message, abort before handling even
        # the first argument and report all invalid arguments at once.
        formatted = ", ".join(f"'{arg}'" for arg in sorted(invalid_args))
        error(
            f"Error - unknown argument(s) {formatted}; aborting, see '-help'."
        )
        sys.exit()

    # Call the appropriate functions to perform the desired tasks
    for arg in task_args:
        match arg:
            case "-test":
                if delist:
                    warn("Task '-test' not supported for delisting candidates")
                if fpc:
                    out("Recounting votes for FP candidates...", heading=True)
                    checkCandidates(
                        Candidate.compareResultToCount,
                        test_log,
                        delist=False,
                        descending=False,
                    )
            case "-close":
                if delist:
                    out("Closing delist candidates...", heading=True)
                    checkCandidates(Candidate.closePage, candidates_page, delist=True)
                if fpc:
                    out("Closing FP candidates...", heading=True)
                    checkCandidates(Candidate.closePage, candidates_page, delist=False)
            case "-info":
                if delist:
                    out("Gathering info about delist candidates...", heading=True)
                    checkCandidates(Candidate.printAllInfo, candidates_page, delist=True)
                if fpc:
                    out("Gathering info about FP candidates...", heading=True)
                    checkCandidates(Candidate.printAllInfo, candidates_page, delist=False)
            case "-park":
                if G_Threads and G_Auto:
                    warn("Auto-parking using threads is disabled for now...")
                    sys.exit()
                if delist:
                    out("Parking delist candidates...", heading=True)
                    checkCandidates(Candidate.park, candidates_page, delist=True)
                if fpc:
                    out("Parking FP candidates...", heading=True)
                    checkCandidates(Candidate.park, candidates_page, delist=False)
            case _:
                # This means we have forgotten to update the invalid_args test.
                error(
                    f"Error - unknown argument '{arg}'; aborting, see '-help'."
                )
                sys.exit()


def signal_handler(signal, frame):
    global G_Abort
    print("\n\nReceived SIGINT, will abort...\n")
    G_Abort = True


signal.signal(signal.SIGINT, signal_handler)

if __name__ == "__main__":
    try:
        main()
    finally:
        pywikibot.stopme()
