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
        self._listPageName = None

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

    def nominator(self, link=True):
        """Return the link to the user that nominated this candidate."""
        history = self.page.revisions(reverse=True, total=1)
        for data in history:
            username = data.user
        if not history:
            return "Unknown"
        if link:
            return "[[User:%s|%s]]" % (username, username)
        else:
            return username

    def creator(self):
        """Return the link to the user that created the image, Not implemented yet."""
        pass

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

        result = self.getResultString()

        new_text = old_text + result

        # Add the featured status to the header
        if self.imageCount() <= 1:
            new_text = self.fixHeader(new_text)

        commit(
            old_text,
            new_text,
            self.page,
            self.getCloseCommitComment()
            + (" (FifthDay=%s)" % ("yes" if fifthDay else "no")),
        )

        return True

    def fixHeader(self, text, value=None):
        """
        Will append the featured status to the header of the candidate
        Will return the new text
        @param value If specified ("yes" or "no" string will be based on it, otherwise isPassed() is used)
        """

        # Check if they are alredy there
        if re.match(r"===.*(%s|%s)===" % (self._proString, self._conString), text):
            return text

        status = ""

        if value:
            if value == "yes":
                status = ", %s" % self._proString
            elif value == "no":
                status = ", %s" % self._conString

        if len(status) < 1:
            status = (
                ", %s" % self._proString
                if self.isPassed()
                else ", %s" % self._conString
            )

        return re.sub(r"(===.*)(===)", r"\1%s\2" % status, text, count=1)

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
        return self.imageCount() > 1

    def sectionCount(self):
        """Count the number of sections in this candidate."""
        text = self.page.get(get_redirect=True)
        return len(re.findall(SectionR, text))

    def imageCount(self):
        """
        Count the number of images that are displayed

        Does not count images that are below a certain threshold
        as they probably are just inline icons and not separate
        edits of this candidate.
        """
        if self._imgCount:
            return self._imgCount

        text = self.page.get(get_redirect=True)

        matches = []
        for m in re.finditer(ImagesR, text):
            matches.append(m)

        count = len(matches)

        if count >= 2:
            # We have several images, check if they are too small to be counted
            for img in matches:

                if re.search(ImagesThumbR, img.group(0)):
                    count -= 1
                else:
                    s = re.search(ImagesSizeR, img.group(0))
                    if s and (int(s.group(1)) <= 150):
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
        """Returns a fixed width title."""
        title = (
            self.cleanSetTitle(keep_set=True) if self.isSet()
            else PrefixR.sub("", self.page.title(), count=1)
        )
        return title[0:50].ljust(50)

    def cleanTitle(self, alternative=False, keepExtension=False):
        """
        Returns the title of the nomination subpage, i.e. normally the name
        of the nominated image, without prefix and (optionally) w/o extension.
        If the 'alternative' parameter is set to True, we operate
        on the 'alternative' filename instead (this is effective *only*
        if the property 'self._alternative' is defined, i.e. during
        the parking procedure of a successful FP candidate).
        """
        if alternative and self._alternative:
            title = re.sub(r"^(?:[Ff]ile|[Ii]mage): *", "", self._alternative)
        else:
            title = PrefixR.sub("", self.page.title(), count=1)
        title = title.rstrip()
        # We must also remove the trailing '/2', '/3' etc. of repeated noms:
        title = re.sub(r"/ *[0-9]+$", "", title)
        if keepExtension:
            return title
        else:
            return re.sub(r"\.\w{2,4}\s*$", "", title)

    def cleanSetTitle(self, keep_set=False):
        """
        Returns the title of a set nomination without the leading
        'Commons:Featured picture candidates/' and (optionally)
        without the 'Set/' part.  Strips leading/trailing whitespace.
        """
        title = self.page.title()
        if match := re.search(r"/ *([Ss]et */(.+))$", title):
            title = match.group(1) if keep_set else match.group(2)
        else:
            error(
                f"Error - called cleanSetTitle() on '{title}' "
                "which does not look like a set."
            )
        return title.strip()

    def fileName(self, alternative=True):
        """
        Return only the filename of this candidate
        This is first based on the title of the page but if that page is not found
        then the first image link in the page is used.
        Will return the new file name if moved.
        @param alternative if false disregard any alternative and return the real filename
        """
        if alternative and self._alternative:
            return self._alternative

        if self._fileName:
            return self._fileName

        # Remove nomination page prefix and use standard 'File:' namespace
        self._fileName = PrefixR.sub("File:", self.page.title())

        if not pywikibot.Page(G_Site, self._fileName).exists():
            match = re.search(ImagesR, self.page.get(get_redirect=True))
            if match:
                self._fileName = match.group(1)

        # Check if file was moved after nomination
        page = pywikibot.Page(G_Site, self._fileName)
        if page.isRedirectPage():
            self._fileName = page.getRedirectTarget().title()

        return self._fileName

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

        # First check if we are already on the page,
        # in that case skip. Can happen if the process
        # have been previously interrupted.
        if re.search(wikipattern(file), old_text):
            out(
                "Skipping addToFeaturedList for '%s', page already listed."
                % self.cleanTitle()
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

    def addToGalleryPage(self, gallery, files):
        """
        Adds the new featured picture (resp. all files from a set nomination)
        to the appropriate featured picture gallery page.
        Should only be called on closed and verified candidates.

        This is ==STEP 2== of the parking procedure.

        @param gallery The gallery link with the name of the gallery page
        and (optionally) a section anchor which denotes the target section
        on that page.
        @param files List with filename(s) of the featured picture or set.
        """
        # The calling code must guarantee that gallery link and files list
        # are not empty.  An assertion can help us to catch bugs:
        assert gallery and files

        # Replace all underscores and non-breaking spaces by plain spaces
        # (underscores are present if users just copy the gallery link,
        # NBSP can be entered by accident with some keyboard settings,
        # e.g. on macOS or Linux)
        gallery = gallery.replace("_", " ").replace("\u00A0", " ")
        # Split the gallery link into gallery page name and section anchor
        # (the latter can be empty)
        link_parts = gallery.split("#", maxsplit=1)
        gallery_page_name = link_parts[0].strip()
        section = link_parts[1].strip() if len(link_parts) > 1 else ""

        # Read the gallery page
        full_page_name = f"Commons:Featured pictures/{gallery_page_name}"
        page = pywikibot.Page(G_Site, full_page_name)
        old_text = page.get(get_redirect=True)
        if self.isSet():
            clean_title = self.cleanSetTitle(keep_set=False)
        else:
            clean_title = self.cleanTitle(alternative=True)

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
                f"Skipping addToGalleryPage() for '{self.page.title()}', "
                "file(s) already listed."
            )
            return
        # Format the new entries and a summary for the message
        new_entries = "".join(f"{file}|{clean_title}\n" for file in new_files)
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
                    f"'{full_page_name}', can't add '{clean_title}'."
                )
                return
            new_text = (
                old_text[:gallery_end_pos]
                + new_entries
                + old_text[gallery_end_pos:]
            )
            message = f"Added {files_for_msg} to the 'Unsorted' section"
        commit(old_text, new_text, page, message)

    def getImagePage(self):
        """Get the image page itself."""
        return pywikibot.Page(G_Site, self.fileName())

    def addAssessments(self, files):
        """
        Adds the {{Assessments}} template to the description page
        of a featured picture, resp. to all files in a set.
        Should only be called on closed and verified candidates.

        This is ==STEP 3== of the parking procedure.

        @param files List with filename(s) of the featured picture or set.
        """
        AssR = re.compile(r"\{\{\s*[Aa]ssessments\s*(\|.*?)\}\}")
        for file in files:
            page = pywikibot.Page(G_Site, file)
            current_page = page
            old_text = page.get(get_redirect=True)
            fn_or = self.fileName(alternative=False)  # Original filename
            fn_al = self.fileName(alternative=True)  # Alternative filename

            # We need the 'com-nom' parameter for sets or if the alternative
            # filename differs from the original filename.
            if self.isSet():
                comnom = "|com-nom=" + self.cleanSetTitle(keep_set=False)
            elif fn_al != fn_or:
                comnom = "|com-nom=" + fn_or.replace("File:", "")
            else:
                comnom = ""

            # First check if there already is an assessments template on the page
            match = re.search(AssR, old_text)
            if match:
                # Make sure to remove any existing com/features or subpage params
                # TODO: 'subpage' is the old name of 'com-nom'. Can be removed later.
                params = re.sub(r"\|\s*featured\s*=\s*\d+", "", match.group(1))
                params = re.sub(r"\|\s*(?:subpage|com-nom)\s*=\s*[^{}|]+", "", params)
                params += "|featured=1"
                params += comnom
                if params[0] != "|":
                    params = "|" + params
                new_text = (
                    old_text[:match.start(0)]
                    + "{{Assessments%s}}" % params
                    + old_text[match.end(0):]
                )
                if new_text == old_text:
                    out(
                        "No change in addAssessments, '%s' already featured."
                        % self.cleanTitle()
                    )
                    return
            else:
                # There is no assessments template so just add it
                if re.search(r"\{\{(?:|\s*)[Ll]ocation", old_text):
                    end = findEndOfTemplate(old_text, "[Ll]ocation")
                elif re.search(r"\{\{(?:|\s*)[Oo]bject[_\s][Ll]ocation", old_text):
                    end = findEndOfTemplate(old_text, r"[Oo]bject[_\s][Ll]ocation")
                else:
                    end = findEndOfTemplate(old_text, "[Ii]nformation")
                new_text = (
                    old_text[:end]
                    + "\n{{Assessments|featured=1%s}}\n" % comnom
                    + old_text[end:]
                )
            commit(old_text, new_text, current_page, "FPC promotion")

    def addToCurrentMonth(self, files):
        """
        Adds the candidate to the monthly overview of new featured pictures.
        Should only be called on closed and verified candidates.

        This is ==STEP 4== of the parking procedure.

        @param files List with filename(s) of the featured picture or set.
        """
        # For set nominations just use the first file.
        file = files[0]

        # Extract voting results
        FinalVotesR = re.compile(
            r"FPC-results-reviewed\|support=([0-9]{0,3})\|oppose=([0-9]{0,3})\|neutral=([0-9]{0,3})\|"
        )
        NomPagetext = self.page.get(get_redirect=True)
        matches = FinalVotesR.finditer(NomPagetext)
        for m in matches:
            if m is None:
                ws = wo = wn = "x"
            else:
                ws = m.group(1)
                wo = m.group(2)
                wn = m.group(3)

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

        # First check if we are already on the page,
        # in that case skip. Can happen if the process
        # have been previously interrupted.
        if re.search(wikipattern(file), old_text):
            out(
                "Skipping addToCurrentMonth for '%s', page already listed."
                % self.cleanTitle()
            )
            return

        # Find the number of lines in the gallery, if AttributeError set count as 1
        m = re.search(r"(?ms)<gallery>(.*)</gallery>", old_text)
        try:
            count = m.group(0).count("\n")
        except AttributeError:
            count = 1

        # We just need to append to the bottom of the gallery
        # with an added title
        # TODO: We lack a good way to find the creator, so it is left out at the moment
        if count == 1:
            old_text = (
                "{{FPArchiveChrono}}\n"
                f"== {month} {year} ==\n"
                "<gallery>\n</gallery>"
            )

        if self.isSet():
            file_title = "'''%s''' - a set of %s files" % (
                self.cleanSetTitle(keep_set=False),
                str(len(files)),
            )
        else:
            file_title = self.cleanTitle(alternative=True)

        new_text = re.sub(
            "</gallery>",
            "%s|%d '''%s''' <br> uploaded by %s, nominated by %s,<br> {{s|%s}}, {{o|%s}}, {{n|%s}} \n</gallery>"
            % (
                file,
                count,
                file_title,
                uploader(file),
                self.nominator(),
                ws,
                wo,
                wn,
            ),
            old_text,
        )

        commit(old_text, new_text, page, "Added [[%s]]" % file)

    def notifyNominator(self, files):
        """
        Add a FP promotion template to the nominator's talk page.
        Should only be called on closed and verified candidates.

        This is ==STEP 5== of the parking procedure.

        @param files List with filename(s) of the featured picture or set.
        """
        talk_link = "User_talk:%s" % self.nominator(link=False)
        talk_page = pywikibot.Page(G_Site, talk_link)

        try:
            old_text = talk_page.get(get_redirect=True)
        except pywikibot.exceptions.NoPageError:
            # Undefined user talk pages are uncommon because every new user
            # is welcomed by an automatic message.  So better stop here.
            warn(
                "notifyNominator: No such page '%s' but ignoring..."
                % talk_link
            )
            return

        fn_or = self.fileName(alternative=False)  # Original filename
        fn_al = self.fileName(alternative=True)  # Alternative filename

        # First check if we are already on the page,
        # in that case skip. Can happen if the process
        # have been previously interrupted.

        # We add the subpage parameter if the original filename
        # differs from the alternative filename.
        subpage = "|subpage=%s" % fn_or if fn_or != fn_al else ""

        # notification for set candidates should add a gallery to talk page and
        # it should be special compared to usual promotions.

        if self.isSet():
            if re.search(r"{{FPpromotionSet\|%s}}" % wikipattern(fn_al), old_text):
                return
            new_text = old_text + "\n\n== Set Promoted to FP ==\n<gallery mode=packed heights=80px>\n%s\n</gallery>\n{{FPpromotionSet|%s%s}} /~~~~" % (
                "\n".join(files),
                fn_al,
                subpage,
            )
            try:
                commit(
                    old_text, new_text, talk_page, "FPC promotion of [[%s]]" % fn_al
                )
            except pywikibot.exceptions.LockedPageError as exc:
                warn(
                    "Page is locked '%s', but ignoring since it's just "
                    "the user notification." % exc
                )
            return
        else:
            pass

        if re.search(r"{{FPpromotion\|%s}}" % wikipattern(fn_or), old_text):
            out(
                "Skipping notifyNominator for '%s', page already listed at '%s'."
                % (self.cleanTitle(), talk_link)
            )
            return

        new_text = old_text + "\n\n== FP Promotion ==\n{{FPpromotion|%s%s}} /~~~~" % (
            fn_al,
            subpage,
        )

        try:
            commit(
                old_text, new_text, talk_page, "FPC promotion of [[%s]]" % fn_al
            )
        except pywikibot.exceptions.LockedPageError as exc:
            warn(
                "Page is locked '%s', but ignoring since it's just "
                "the user notification." % exc
            )

    def notifyUploader(self, files):
        """
        Add a FP promotion template to the uploader's talk page.
        Should only be called on closed and verified candidates.

        This is ==STEP 6== of the parking procedure.

        To understand this method and how it differs from notifyNominator(),
        please keep in mind that all files in a set nomination have the same
        nominator, but they may have been uploaded by different users.
        That's very unusual and discouraged by the current FPC rules,
        but the bot stills supports that special case.  Therefore this method
        handles the files one by one, unlike notifyNominator().

        @param files List with filename(s) of the featured picture or set.
        """
        undefined_or_locked_talk_pages = set()
        nominator_name = self.nominator(link=False)
        for file in files:
            # Check if nominator and uploader are the same user,
            # to avoid adding two templates to the same talk page
            uploader_name = uploader(file, link=False)
            if nominator_name == uploader_name:
                out(
                    f"Skipping notifyUploader() for '{file}', "
                    "uploader is identical to nominator."
                )
                continue

            # Find and read the uploader's talk page
            talk_link = "User talk:" + uploader_name
            if talk_link in undefined_or_locked_talk_pages:
                # Don't load or report undefined or locked talk pages twice
                continue
            talk_page = pywikibot.Page(G_Site, talk_link)
            try:
                old_text = talk_page.get(get_redirect=True)
            except pywikibot.exceptions.NoPageError:
                # Undefined user talk pages are uncommon because every new user
                # is welcomed by an automatic message.  So better stop here.
                warn(
                    f"The user talk page '{talk_link}' is undefined, "
                    "but ignoring since it's just the uploader notification."
                )
                undefined_or_locked_talk_pages.add(talk_link)
                continue

            # We need the 'subpage' parameter for sets or if the alternative
            # filename differs from the original filename.
            # NB that in this case we must keep the 'Set/' prefix.
            fn_or = self.fileName(alternative=False)  # Original filename
            fn_al = self.fileName(alternative=True)  # Alternative filename
            if self.isSet():
                subpage = "|subpage=" + self.cleanSetTitle(keep_set=True)
                fn_al = file
            elif fn_al != fn_or:
                subpage = "|subpage=" + fn_or
            else:
                subpage = ""
            template = f"{{{{FPpromotedUploader|{fn_al}{subpage}}}}}"

            # Check if there already is a promotion template for the file
            # on the user talk page.  If yes, we skip that file,
            # but continue to check the other files (for set nominations).
            if re.search(wikipattern(template), old_text):
                out(
                    f"Skipping notifyUploader() for '{file}', "
                    f"promotion template is already present at '{talk_link}'."
                )
                continue

            # Update the description and commit the new text
            new_text = (
                f"{old_text.rstrip()}\n"
                "\n"
                "== FP Promotion ==\n"
                f"{template} /~~~~"
            )
            message = f"FPC promotion of [[{fn_al}]]"
            try:
                commit(old_text, new_text, talk_page, message)
            except pywikibot.exceptions.LockedPageError:
                warn(
                    f"The user talk page '{talk_link}' is locked, "
                    "but ignoring since it's just the uploader notification."
                )
                undefined_or_locked_talk_pages.add(talk_link)

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

        # Check that the nomination subpage actually exists
        if not self.page.exists():
            error("%s: (Error: no such page?!)" % self.cutTitle())
            return

        # First look for verified results
        # (leaving out stricken or commented results which have been corrected)
        text = self.page.get(get_redirect=True)
        redacted_text = filter_content(text)
        results = self._VerifiedR.findall(redacted_text)
        # Stop if there is not exactly one valid verified result
        if not results:
            out("%s: (ignoring, no verified results)" % self.cutTitle())
            return
        if len(results) > 1:
            out("%s: (ignoring, several verified results?)" % self.cutTitle())
            return
        if self.isWithdrawn():
            out("%s: (ignoring, was withdrawn)" % self.cutTitle())
            return
        if self.isFPX():
            out("%s: (ignoring, was FPXed)" % self.cutTitle())
            return

        # Check that the image page(s) exist, if not ignore this candidate
        if self.isSet():
            set_files = self.setFiles()
            if not set_files:
                error("%s: (Error: found no images in set)" % self.cutTitle())
                return
            for file in set_files:
                if not pywikibot.Page(G_Site, file).exists():
                    error(
                        "%s: (Error: can't find set image '%s')"
                        % (self.cutTitle(), file)
                    )
                    return
        elif not pywikibot.Page(G_Site, self.fileName()).exists():
            error("%s: (Error: can't find image page)" % self.cutTitle())
            return

        # We should now have a candidate with verified result that we can park
        verified_result = results[0]
        success = verified_result[3]
        if success in {"yes", "no"}:
            # If the suffix to the title has not been added, add it now
            new_text = self.fixHeader(text, success)
            if new_text != text:
                commit(text, new_text, self.page, "Fixed header")
            # Park the candidate
            if success == "yes":
                self.handlePassedCandidate(verified_result)
            else:
                self.moveToLog(self._conString)
        else:
            out(
                "%s: (Error: unknown verified feature status '%s')"
                % (self.cutTitle(), success)
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

    def __init__(self, page):
        """
        The initializer calls the superclass initializer in order to set
        instance variables to the appropriate values for this class.

        @param page A pywikibot.Page object for the nomination subpage.
        """
        super().__init__(
            page,
            SupportR,
            OpposeR,
            NeutralR,
            "featured",
            "not featured",
            ReviewedTemplateR,
            CountedTemplateR,
            VerifiedResultR,
        )
        self._listPageName = "Commons:Featured picture candidates/candidate list"

    def getResultString(self):
        if self.imageCount() > 1:
            return "\n\n{{FPC-results-unreviewed|support=X|oppose=X|neutral=X|featured=no|gallery=|alternative=|sig=<small>'''Note: this candidate has several alternatives, thus if featured the alternative parameter needs to be specified.'''</small> /~~~~)}}"
        else:
            return "\n\n{{FPC-results-unreviewed|support=%d|oppose=%d|neutral=%d|featured=%s|gallery=%s|sig=~~~~}}" % (
                self._pro,
                self._con,
                self._neu,
                "yes" if self.isPassed() else "no",
                self.findGalleryOfFile(),
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
        # Some methods need the full gallery link with section anchor,
        # others only the gallery page name or even just the basic gallery.
        full_gallery_link = results[4].strip()
        gallery_page = re.sub(r"#.*", "", full_gallery_link).rstrip()
        if not gallery_page:
            out("%s: (ignoring, gallery not defined)" % self.cutTitle())
            return
        basic_gallery = re.search(r"^(.*?)(?:/|$)", gallery_page).group(1)

        # Check if we have an alternative for a multi image
        if self.imageCount() > 1:
            if len(results) > 5 and len(results[5]):
                if not pywikibot.Page(G_Site, results[5]).exists():
                    out("%s: (ignoring, specified alternative not found)" % results[5])
                else:
                    self._alternative = results[5]
            else:
                out("%s: (ignoring, alternative not set)" % self.cutTitle())
                return

        # Promote the new featured picture(s)
        files = self.setFiles() if self.isSet() else [self.fileName()]
        if not files:
            out("%s: (ignoring, no file(s) found)" % self.cutTitle())
            return
        self.addToFeaturedList(basic_gallery, files)
        self.addToGalleryPage(full_gallery_link, files)
        self.addAssessments(files)
        self.addToCurrentMonth(files)
        self.notifyNominator(files)
        self.notifyUploader(files)
        self.moveToLog(self._proString)


class DelistCandidate(Candidate):
    """A delisting candidate."""

    def __init__(self, page):
        """
        The initializer calls the superclass initializer in order to set
        instance variables to the appropriate values for this class.

        @param page A pywikibot.Page object for the nomination subpage.
        """
        super().__init__(
            page,
            DelistR,
            KeepR,
            NeutralR,
            "delisted",
            "not delisted",
            DelistReviewedTemplateR,
            DelistCountedTemplateR,
            VerifiedDelistResultR,
        )
        self._listPageName = "Commons:Featured picture candidates/candidate list"

    def getResultString(self):
        return (
            "\n\n{{FPC-delist-results-unreviewed|delist=%d|keep=%d|neutral=%d|delisted=%s|sig=~~~~}}"
            % (self._pro, self._con, self._neu, "yes" if self.isPassed() else "no")
        )

    def getCloseCommitComment(self):
        return "Closing for review (%d delist, %d keep, %d neutral, delisted=%s)" % (
            self._pro,
            self._con,
            self._neu,
            "yes" if self.isPassed() else "no",
        )

    def handlePassedCandidate(self, results):
        # Delistings does not care about the gallery
        self.removeFromFeaturedLists(results)
        self.removeAssessments()
        self.moveToLog(self._proString)

    def removeFromFeaturedLists(self, results):
        """Remove a candidate from all featured lists."""
        # We skip checking the page with the 4 newest images
        # the chance that we are there is very small and even
        # if we are we will soon be rotated away anyway.
        # So just check and remove the candidate from any gallery pages
        references = self.getImagePage().getReferences(with_template_inclusion=False)
        for ref in references:
            if ref.title().startswith("Commons:Featured pictures/"):
                if ref.title().startswith("Commons:Featured pictures/chronological"):
                    out("Adding delist note to %s" % ref.title())
                    old_text = ref.get(get_redirect=True)
                    now = datetime.datetime.now(datetime.UTC)
                    new_text = re.sub(
                        r"(([Ff]ile|[Ii]mage):%s.*)\n"
                        % wikipattern(self.cleanTitle(keepExtension=True)),
                        r"\1 '''Delisted %d-%02d-%02d (%s-%s)'''\n"
                        % (now.year, now.month, now.day, results[1], results[0]),
                        old_text,
                    )
                    commit(
                        old_text, new_text, ref, "Delisted [[%s]]" % self.fileName()
                    )
                else:
                    old_text = ref.get(get_redirect=True)
                    new_text = re.sub(
                        r"(\[\[)?([Ff]ile|[Ii]mage):%s.*\n"
                        % wikipattern(self.cleanTitle(keepExtension=True)),
                        "",
                        old_text,
                    )
                    commit(
                        old_text, new_text, ref, "Removing [[%s]]" % self.fileName()
                    )

    def removeAssessments(self):
        """Remove FP status from an image."""
        imagePage = self.getImagePage()
        old_text = imagePage.get(get_redirect=True)

        # First check for the old {{Featured picture}} template
        new_text = re.sub(
            r"{{[Ff]eatured[ _]picture}}", "{{Delisted picture}}", old_text
        )

        # Then check for the assessments template
        # The replacement string needs to use the octal value for the char '2' to
        # not confuse python as '\12\2' would obviously not work
        new_text = re.sub(
            r"({{[Aa]ssessments\s*\|.*(?:com|featured)\s*=\s*)1(.*?}})",
            r"\1\062\2",
            new_text,
        )

        commit(old_text, new_text, imagePage, "Delisted")


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


def findCandidates(page_name, delist):
    """
    Returns a list with candidate objects for all nomination subpages,
    either from the page with the current candidates or from a log page
    with closed nominations.
    The list retains the original order of entries and omits damaged links.
    If we find redirects to renamed nomination subpages, they are resolved
    (so the returned candidate objects point to the actual nominations)
    and the page with the list of candidates is updated.

    @param page_name The name either of the page with the current candidates
        or of the log page that we want to check.
    @param delist    Specify True to get only delist nominations,
        False to get only FP nominations.
    """
    # Extract nomination subpage names
    out(
        f"Extracting {'delist' if delist else 'FP'} candidates, "
        "checking for redirects..."
    )
    page = pywikibot.Page(G_Site, page_name)
    old_text = page.get(get_redirect=True)
    without_comments = re.sub(r"<!--.+?-->", "", old_text, flags=re.DOTALL)
    subpage_entries = re.findall(
        r"(\{\{ *(Commons:Featured[ _]picture[ _]candidates */[^\n}]+?)\}\})",
        without_comments,
    )
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
            error(
                f"Error - nomination '{subpage.title()}' not found, ignoring"
            )
            continue
        # Check for redirects and and resolve them
        if subpage.isRedirectPage():
            try:
                subpage = subpage.getRedirectTarget()
            except (pywikibot.exceptions.CircularRedirectError, RuntimeError):
                # Circular or invalid redirect
                error(
                    "Error - invalid nomination redirect page "
                    f"'{subpage.title()}', ignoring"
                )
                continue
            new_name = subpage.title()
            out(f"Nomination '{subpage_name}' has been renamed to '{new_name}'")
            redirects.append((full_entry, f'{{{{{new_name}}}}}'))
        # OK, seems the nomination is fine -- append candidate object
        candidates.append(candidate_class(subpage))

    # If we have found any redirects, update the candidates page
    if redirects:
        new_text = old_text
        for full_entry, new_entry in redirects:
            new_text = new_text.replace(full_entry, new_entry, count=1)
        message = (
            f"Resolved {len(redirects)} redirect(s) to renamed nomination(s)"
        )
        commit(old_text, new_text, page, message)
    return candidates


def checkCandidates(check, page, delist, descending=True):
    """
    Calls a function on each candidate found on the specified page.

    @param check      A method of the Candidate class which should be called
        on each candidate.
    @param page       A page which includes all nominations as templates;
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

    candidates = findCandidates(page, delist)
    if not candidates:
        out(
            f"Found no {'delist' if delist else 'FP'} candidates"
            f"{' matching the -match argument' if G_MatchPattern else ''}."
        )
        return
    if descending:
        candidates.reverse()

    tot = len(candidates)
    for i, candidate in enumerate(candidates, start=1):

        if not G_Threads:
            out("(%03d/%03d) " % (i, tot), newline=False, date=True)

        try:
            if G_Threads:
                while threading.active_count() >= config.max_external_links:
                    time.sleep(0.1)
                thread = ThreadCheckCandidate(candidate, check)
                thread.start()
            else:
                check(candidate)
        except pywikibot.exceptions.NoPageError as exc:
            error("No such page '%s'" % exc)
        except pywikibot.exceptions.LockedPageError as exc:
            error("Page is locked '%s'" % exc)

        if G_Abort:
            break


def filter_content(text):
    """
    Will filter away content that should not be parsed.

    Currently this includes:
    * The <s> tag for striking out votes
    * The <nowiki> tag which is just for displaying syntax
    * Image notes
    * Html comments

    """
    text = strip_tag(text, "[Ss]")
    text = strip_tag(text, "[Nn]owiki")
    text = strip_tag(text, "[Ss]trike")
    text = strip_tag(text, "[Dd]el")
    text = re.sub(
        r"(?s){{\s*[Ii]mageNote\s*\|.*?}}.*{{\s*[iI]mageNoteEnd.*?}}", "", text
    )
    text = re.sub(r"(?s)<!--.*?-->", "", text)
    return text


def strip_tag(text, tag):
    """Will simply take a tag and remove a specified tag."""
    return re.sub(r"(?s)<%s>.*?</%s>" % (tag, tag), "", text)


def uploader(file, link=True):
    """Return the link to the user that uploaded the nominated image."""
    page = pywikibot.Page(G_Site, file)
    history = page.revisions(reverse=True, total=1)
    for data in history:
        username = data.user
    if not history:
        return "Unknown"
    if link:
        return "[[User:%s|%s]]" % (username, username)
    else:
        return username


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

#
# Compiled regular expressions follows
#

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
ImagesR = re.compile(r"\[\[((?:[Ff]ile|[Ii]mage):[^|]+).*?\]\]")
# Look for a size specification of the image link
ImagesSizeR = re.compile(r"\|.*?(\d+)\s*px")
# Find if there is a thumb parameter specified
ImagesThumbR = re.compile(r"\|\s*thumb\b")
# Finds the last image link on a page
LastImageR = re.compile(
    r"(?s)(\[\[(?:[Ff]ile|[Ii]mage):[^\n]*\]\])(?!.*\[\[(?:[Ff]ile|[Ii]mage):)"
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
    candidates_page = "Commons:Featured picture candidates/candidate_list"
    testLog = "Commons:Featured_picture_candidates/Log/January_2025"
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
                        testLog,
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
