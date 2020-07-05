# -*- coding: utf-8 -*-
"""
This bot runs as FPCBot on wikimedia commons
It implements vote counting and supports bot runs as FPCBot on wikimedia commons
It implements vote counting and supports
moving the finished nomination to the archive.

Programmed by Daniel78 at Commons.

It adds the following commandline arguments:

-test             Perform a testrun against an old log
-close            Close and add result to the nominations
-info             Just print the vote count info about the current nominations
-park             Park closed and verified candidates
-auto             Do not ask before commiting edits to articles
-dry              Do not submit any edits, just print them
-threads          Use threads to speed things up, can't be used in interactive mode
-fpc              Handle the featured candidates (if neither -fpc or -delist is used all candidates are handled)
-delist           Handle the delisting candidates (if neither -fpc or -delist is used all candidates are handled)
-notime           Avoid displaying timestamps in log output
-match pattern    Only operate on candidates matching this pattern
"""

import pywikibot, re, datetime, sys, signal

# Imports needed for threading
import threading, time
from pywikibot import config

# Import for single process check
# dependency can be installed using "easy_install tendo"
from tendo import singleton


class NotImplementedException(Exception):

    """Not implemented."""


class ThreadCheckCandidate(threading.Thread):
    def __init__(self, candidate, check):
        threading.Thread.__init__(self)
        self.candidate = candidate
        self.check = check

    def run(self):
        self.check(self.candidate)


class Candidate:
    """
    This is one picture candidate

    This class just serves as base for the DelistCandidate and FPCandidate classes
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
        """Page is a pywikibot.Page object ."""
        # Later perhaps this can be cleaned up by letting the subclasses keep the variables
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
        self._listPageName = None

    def printAllInfo(self):
        """
        Console output of all information sought after
        """
        try:
            self.countVotes()
            out(
                "%s: S:%02d O:%02d N:%02d D:%02d De:%02d Se:%d Im:%02d W:%s (%s)"
                % (
                    self.cutTitle(),
                    self._pro,
                    self._con,
                    self._neu,
                    self.daysOld(),
                    self.daysSinceLastEdit(),
                    self.sectionCount(),
                    self.imageCount(),
                    self.isWithdrawn(),
                    self.statusString(),
                )
            )
        except pywikibot.NoPage:
            out("%s: -- No such page -- " % self.cutTitle(), color="lightred")

    def nominator(self, link=True):
        """Return the link to the user that nominated this candidate."""
        history = self.page.revisions(reverse=True, total=1)
        for data in history:
            username = (data.user)
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
        """Check if the nomination page has "/[Ss]et/" in it, if yes it must be a set nomination."""
        if re.search(r"/[Ss]et", self.page.title()):
            return True
        else:
            return False
    
    def setFiles(self):
        """Try to return list of all files in a set, files in the last gallery in the nomination page."""
        m = re.search(r"<gallery([^\]]*)</gallery>", self.page.get(get_redirect=True))
        text_inside_gallery = m.group(1)
        filesList = []
        for line in text_inside_gallery.splitlines():
            if line.startswith('File:'):
                files = re.sub(r"\|.*", "", line)
                filesList.append(files)
        return filesList

    def findGalleryOfFile(self):
        """Try to find Gallery in the nomination page to make closing users life easier."""
        text = self.page.get(get_redirect=True)
        RegexGallery = re.compile(r'(?:.*)Gallery(?:.*)(?:\s.*)\[\[Commons\:Featured[_ ]pictures\/([^\]]{1,180})')
        matches = RegexGallery.finditer(text)
        for m in matches:
            Gallery = (m.group(1))
        try:
            Gallery
        except:
            Gallery = ""

        return Gallery

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
            out("Warning - %s has no content" % self.page, color="lightred")

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

    def closePage(self):
        """
        Will add the voting results to the page if it is finished.
        If it was, True is returned else False
        """

        # First make a check that the page actually exist:
        if not self.page.exists():
            out('"%s" no such page?!' % self.cutTitle())
            return

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
            out("Warning - %s has no content" % self.page, color="lightred")
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

        self.commit(
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

        return re.sub(r"(===.*)(===)", r"\1%s\2" % status, text, 1)

    def getResultString(self):
        """Must be implemented by the subclasses (Text to add to closed pages)."""
        raise NotImplementedException()

    def getCloseCommitComment(self):
        """Must be implemened by the subclasses (Commit comment for closed pages)."""
        raise NotImplementedException()

    def creationTime(self):
        """
        Find the time that this candidate was created
        If we can't find the creation date, for example due to
        the page not existing we return now() such that we
        will ignore this nomination as too young.
        """
        if self._creationTime:
            return self._creationTime

        history = self.page.revisions(reverse=True, total=1)

        if not history:
            out(
                "Could not retrieve history for '%s', returning now()"
                % self.page.title()
            )
            return datetime.datetime.now()

        for data in history:
            self._creationTime = (data['timestamp'])

        # print "C:" + self._creationTime.isoformat()
        # print "N:" + datetime.datetime.utcnow().isoformat()
        return self._creationTime

    def statusString(self):
        """Short status string about the candidate."""
        if self.isIgnored():
            return "Ignored"
        elif self.isWithdrawn():
            return "Withdrawn"
        elif not self.isDone():
            return "Active"
        else:
            return self._proString if self.isPassed() else self._conString

    def daysOld(self):
        """Find the number of days this nomination has existed."""
        if self._daysOld != -1:
            return self._daysOld

        delta = datetime.datetime.utcnow() - self.creationTime()
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
            lastEdit = datetime.datetime.strptime(
                str(self.page.editTime()), "%Y-%m-%dT%H:%M:%SZ"
            )
        except:
            return -1

        delta = datetime.datetime.utcnow() - lastEdit
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
        Will scan this nomination and check whether it has
        already been closed, and if so parses for the existing
        result.
        The return value is a list of tuples, and normally
        there should only be one such tuple. The tuple
        contains four values:
        support,oppose,neutral,(featured|not featured)
        """
        text = self.page.get(get_redirect=True)
        return re.findall(PreviousResultR, text)

    def compareResultToCount(self):
        """
        If there is an existing result we will compare
        it to a new vote count made by this bot and
        see if they match. This is for testing purposes
        of the bot and to find any incorrect old results.
        """
        res = self.existingResult()

        if self.isWithdrawn():
            out("%s: (ignoring, was withdrawn)" % self.cutTitle())
            return

        elif self.isFPX():
            out("%s: (ignoring, was FPXed)" % self.cutTitle())
            return

        elif not res:
            out("%s: (ignoring, has no results)" % self.cutTitle())
            return

        elif len(res) > 1:
            out("%s: (ignoring, has several results)" % self.cutTitle())
            return

        # We have one result, so make a vote count and compare
        old_res = res[0]
        was_featured = old_res[3] == "featured"
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
        return re.sub(PrefixR, "", self.page.title())[0:50].ljust(50)

    def cleanTitle(self, keepExtension=False):
        """
        Returns a title string without prefix and extension
        Note that this always operates on the original title and that
        a possible change by the alternative parameter is not considered,
        but maybe it should be ?
        """
        noprefix = re.sub(PrefixR, "", self.page.title())
        if keepExtension:
            return noprefix
        else:
            return re.sub(r"\.\w{1,3}$\s*", "", noprefix)

    def fileName(self, alternative=True):
        """
        Return only the filename of this candidate
        This is first based on the title of the page but if that page is not found
        then the first image link in the page is used.
        Will return the new file name if moved.
        @param alternative if false disregard any alternative and return the real filename
        """
        # The regexp here also removes any possible crap between the prefix
        # and the actual start of the filename.
        if alternative and self._alternative:
            return self._alternative

        if self._fileName:
            return self._fileName

        self._fileName = re.sub(
            "(%s.*?)([Ff]ile|[Ii]mage)" % candPrefix, r"\2", self.page.title()
        )

        if not pywikibot.Page(G_Site, self._fileName).exists():
            match = re.search(ImagesR, self.page.get(get_redirect=True))
            if match:
                self._fileName = match.group(1)

        #Check if file was moved after nomination
        page = pywikibot.Page(G_Site, self._fileName)
        if page.isRedirectPage():
            self._fileName = page.getRedirectTarget().title()

        return self._fileName

    def addToFeaturedList(self, gallery):
        """
        Will add this page to the list of featured images.
        This uses just the base of the gallery, like 'Animals'.
        Should only be called on closed and verified candidates

        This is ==STEP 1== of the parking procedure

        @param gallery The categorization gallery
        """
        if self.isSet():
            file = (self.setFiles())[0] # Add the first file from gallery.
        else:
            file = self.fileName()

        listpage = "Commons:Featured pictures, list"
        page = pywikibot.Page(G_Site, listpage)
        old_text = page.get(get_redirect=True)

        # First check if we are already on the page,
        # in that case skip. Can happen if the process
        # have been previously interrupted.
        if re.search(wikipattern(file), old_text):
            out(
                "Skipping addToFeaturedList for '%s', page already listed."
                % self.cleanTitle(),
                color="lightred",
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
        self.commit(old_text, new_text, page, "Added [[%s]]" % file)

    def addToCategorizedFeaturedList(self, gallery):
        """
        Adds the candidate to the gallery of
        pictures. This is the full gallery with
        the section in that particular page.

        This is ==STEP 2== of the parking procedure

        @param gallery The categorization gallery
        If it's a set, will add all file from the list,
        else just one if single nomination.
        """
        if self.isSet():
            files = self.setFiles()
        else:
            files = []
            files.append(self.fileName())
        for file in files:
            gallery_full_path = "Commons:Featured pictures/" + re.sub(r"#.*", "", gallery)
            page = pywikibot.Page(G_Site, gallery_full_path)
            old_text = page.get(get_redirect=True)
            section_R = r"#(.*)"
            m = re.search(section_R, gallery)
            try:
                section = m.group(1)
            except AttributeError:
                section = None

            if section != None:

                # Trying to generate a regex for finding the section in a gallery if specified in nomination
                # First we are escaping all parentheses, as they are used in regex
                # Replacement of all uunderscore with \s , some users just copy the url
                # Replacing all \s with \s(?:\s*|)\s, user have linked the section to categories. Why ? To make our lives harder

                section = section.replace(")","\)").replace("(","\(").replace("_"," ").replace(" ", " (?:\[{2}|\]{2}|) ")
                section_search_R = (section  +  r"(?:(?:[^\{\}]|\n)*?)(</gallery>)").replace(" ", "(?:\s*|)")
                m = re.search(section_search_R, old_text)
                try:
                    old_section = m.group()
                except AttributeError:
                    section = None

            # First check if we are already on the page,
            # in that case skip. Can happen if the process
            # have been previously interrupted.

            if re.search(wikipattern(file), old_text):
                out(
                    "Skipping addToCategorizedFeaturedList for '%s', page already listed."
                    % self.cleanTitle(),
                    color="lightred",
                )
                return

            # If we found a section, we try to add the image in the section else add to the bottom most gallery (unsorted)
            if section != None:
                file_info = "%s|%s\n</gallery>" % (file, self.cleanTitle())
                updated_section = re.sub(r"</gallery>", file_info, old_section)
                new_text = old_text.replace(old_section, updated_section)
            else:
                # We just need to append to the bottom of the gallery with an added title
                # The regexp uses negative lookahead such that we place the candidate in the
                # last gallery on the page.
                new_text = re.sub(
                    "(?s)</gallery>(?!.*</gallery>)",
                    "%s|%s\n</gallery>" % (file, self.cleanTitle()),
                    old_text,
                    1,
                )

            self.commit(old_text, new_text, page, "Added [[%s]]" % file)

    def getImagePage(self):
        """Get the image page itself."""
        return pywikibot.Page(G_Site, self.fileName())

    def addAssessments(self):
        """
        Adds the the assessments template to a featured
        pictures descripion page.
        This is ==STEP 3== of the parking procedure
        Will add assessments to all files in a set
        """

        if self.isSet():
            files = self.setFiles()
        else:
            files = []
            files.append(self.fileName())
        for file in files:
            page = pywikibot.Page(G_Site, file)
            current_page = page
            old_text = page.get(get_redirect=True)
            AssR = re.compile(r"{{\s*[Aa]ssessments\s*\|(.*)}}")
            fn_or = self.fileName(alternative=False)  # Original filename
            fn_al = self.fileName(alternative=True)  # Alternative filename
            # We add the com-nom parameter if the original filename
            # differs from the alternative filename.
            comnom = "|com-nom=%s" % fn_or.replace("File:", "") if fn_or != fn_al else ""
            
            # The template needs the com-nom to link to the site from file page
            if self.isSet():
                comnom = "|com-nom="+(re.search(r"/[Ss]et/(.*)", self.page.title())).group(1)
            else:
                pass

            # First check if there already is an assessments template on the page
            params = re.search(AssR, old_text)
            if params:
                # Make sure to remove any existing com/features or subpage params
                # TODO: 'com' will be obsolete in the future and can then be removed
                # TODO: 'subpage' is the old name of com-nom. Can be removed later.
                params = re.sub(r"\|\s*(?:featured|com)\s*=\s*\d+", "", params.group(1))
                params = re.sub(r"\|\s*(?:subpage|com-nom)\s*=\s*[^{}|]+", "", params)
                params += "|featured=1"
                params += comnom
                if params.find("|") != 0:
                    params = "|" + params
                new_ass = "{{Assessments%s}}" % params
                new_text = re.sub(AssR, new_ass, old_text)
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
                    end = findEndOfTemplate(old_text, "[Oo]bject[_\s][Ll]ocation")
                else:
                    end = findEndOfTemplate(old_text, "[Ii]nformation")
                

                new_text = (
                    old_text[:end]
                    + "\n{{Assessments|featured=1%s}}\n" % comnom
                    + old_text[end:]
                )
                # new_text = re.sub(r'({{\s*[Ii]nformation)',r'{{Assessments|featured=1}}\n\1',old_text)
                self.commit(old_text, new_text, current_page, "FPC promotion")

    def addToCurrentMonth(self):
        """
        Adds the candidate to the list of featured picture this month

        This is ==STEP 4== of the parking procedure
        """
        if self.isSet():
            files = (self.setFiles())[:1] # The first file from gallery.
        else:
            files = []
            files.append(self.fileName())
        for file in files:
            FinalVotesR = re.compile(r'FPC-results-reviewed\|support=([0-9]{0,3})\|oppose=([0-9]{0,3})\|neutral=([0-9]{0,3})\|')
            NomPagetext = self.page.get(get_redirect=True)
            matches = FinalVotesR.finditer(NomPagetext)
            for m in matches:
                if m is None:
                    ws=wo=wn= "x"
                else:
                    ws = m.group(1)
                    wo = m.group(2)
                    wn = m.group(3)

            today = datetime.date.today()
            monthpage = "Commons:Featured_pictures/chronological/%s %s" % (datetime.datetime.utcnow().strftime("%B"), today.year,)
            page = pywikibot.Page(G_Site, monthpage)
            try:
                old_text = page.get(get_redirect=True)
            except pywikibot.NoPage:
                old_text = ""
            # First check if we are already on the page,
            # in that case skip. Can happen if the process
            # have been previously interrupted.
            if re.search(wikipattern(file), old_text):
                out(
                    "Skipping addToCurrentMonth for '%s', page already listed."
                    % self.cleanTitle(),
                    color="lightred",
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

            if count ==1:
                old_text = "{{subst:FPArchiveChrono}}\n== %s %s ==\n<gallery>\n</gallery>" % (datetime.datetime.utcnow().strftime("%B"), today.year,)
            else:pass
            
            if self.isSet():
                file_title = "'''%s''' - a set of %s files" % ((re.search(r"/[Ss]et/(.*)", self.page.title())).group(1), str(len(self.setFiles())))
            else:
                file_title = self.cleanTitle()
                

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

            self.commit(old_text, new_text, page, "Added [[%s]]" % file)

    def notifyNominator(self):
        """
        Add a template to the nominators talk page

        This is ==STEP 5== of the parking procedure
        """
        talk_link = "User_talk:%s" % self.nominator(link=False)
        talk_page = pywikibot.Page(G_Site, talk_link)

        try:
            old_text = talk_page.get(get_redirect=True)
        except pywikibot.NoPage:
            out(
                "notifyNominator: No such page '%s' but ignoring..." % talk_link,
                color="lightred",
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
            files_newline_string = converttostr(self.setFiles(), '\n')
            new_text = old_text + "\n\n== Set Promoted to FP ==\n<gallery mode=packed heights=80px>%s\n</gallery>\n{{FPpromotionSet|%s%s}} /~~~~" % (
                files_newline_string,
                fn_al,
                subpage,
            )
            try:
                self.commit(
                    old_text, new_text, talk_page, "FPC promotion of [[%s]]" % fn_al
                )
            except pywikibot.LockedPage as error:
                out(
                    "Page is locked '%s', but ignoring since it's just the user notification."
                    % error,
                    color="lightyellow",
                )
            return
        else:
            pass


        if re.search(r"{{FPpromotion\|%s}}" % wikipattern(fn_or), old_text):
            out(
                "Skipping notifyNominator for '%s', page already listed at '%s'."
                % (self.cleanTitle(), talk_link),
                color="lightred",
            )
            return

        new_text = old_text + "\n\n== FP Promotion ==\n{{FPpromotion|%s%s}} /~~~~" % (
            fn_al,
            subpage,
        )

        try:
            self.commit(
                old_text, new_text, talk_page, "FPC promotion of [[%s]]" % fn_al
            )
        except pywikibot.LockedPage as error:
            out(
                "Page is locked '%s', but ignoring since it's just the user notification."
                % error,
                color="lightyellow",
            )

    def notifyUploader(self):
        """
        Add a template to the uploaders talk page
        This is ==STEP 6== of the parking procedure
        """
        if self.isSet():
            files = self.setFiles()
        else:
            files = []
            files.append(self.fileName())
        
        for file in files:
            #Check if nominator and uploaders are same, avoiding adding a template twice
            if self.nominator() == uploader(file, link=True):
                continue

            talk_link = "User_talk:%s" % uploader(file, link=False)
            talk_page = pywikibot.Page(G_Site, talk_link)
            try:
                old_text = talk_page.get(get_redirect=True)
            except pywikibot.NoPage:
                out(
                    "notifyUploader: No such page '%s' but ignoring..." % talk_link,
                    color="lightred",
                )
                return

            fn_or = self.fileName(alternative=False)  # Original filename
            fn_al = self.fileName(alternative=True)  # Alternative filename

            # First check if we are already on the page,
            # in that case skip. Can happen if the process
            # have been previously interrupted.

            if re.search(r"{{FPpromotion\|%s}}" % wikipattern(fn_or), old_text):
                out(
                    "Skipping notifyUploader for '%s', page already listed at '%s'."
                    % (self.cleanTitle(), talk_link),
                    color="lightred",
                )
                return

            # We add the subpage parameter if the original filename
            # differs from the alternative filename.

            subpage = "|subpage=%s" % fn_or if fn_or != fn_al else ""
            
            if self.isSet():
                subpage = "|subpage="+(re.search(r"[Ss]et/(.*)", self.page.title())).group(0)
                fn_al = file

            new_text = old_text + "\n\n== FP Promotion ==\n{{FPpromotedUploader|%s%s}} /~~~~" % (
                fn_al,
                subpage,
            )

            try:
                self.commit(
                    old_text, new_text, talk_page, "FPC promotion of [[%s]]" % fn_al
                )
            except pywikibot.LockedPage as error:
                out(
                    "Page is locked '%s', but ignoring since it's just the user notification."
                    % error,
                    color="lightyellow",
                )


    def moveToLog(self, reason=None):
        """
        Remove this candidate from the current list
        and add it to the log of the current month

        This is ==STEP 7== of the parking procedure
        """

        why = (" (%s)" % reason) if reason else ""

        # Add to log
        # (Note FIXME, we must probably create this page if it does not exist)
        today = datetime.date.today()
        current_month = datetime.datetime.utcnow().strftime("%B")
        log_link = "Commons:Featured picture candidates/Log/%s %s" % (
            current_month,
            today.year,
        )
        log_page = pywikibot.Page(G_Site, log_link)

        # If the page does not exist we just create it ( put does that automatically )
        try:
            old_log_text = log_page.get(get_redirect=True)
        except pywikibot.NoPage:
            old_log_text = ""

        if re.search(wikipattern(self.fileName()), old_log_text):
            out(
                "Skipping add in moveToLog for '%s', page already there"
                % self.cleanTitle(),
                color="lightred",
            )
        else:
            new_log_text = old_log_text + "\n{{%s}}" % self.page.title()
            self.commit(
                old_log_text,
                new_log_text,
                log_page,
                "Adding [[%s]]%s" % (self.fileName(), why),
            )

        # Remove from current list
        candidate_page = pywikibot.Page(G_Site, self._listPageName)
        old_cand_text = candidate_page.get(get_redirect=True)
        new_cand_text = re.sub(
            r"{{\s*%s\s*}}.*?\n?" % wikipattern(self.page.title()), "", old_cand_text
        )

        if old_cand_text == new_cand_text:
            out(
                "Skipping remove in moveToLog for '%s', no change." % self.cleanTitle(),
                color="lightred",
            )
        else:
            self.commit(
                old_cand_text,
                new_cand_text,
                candidate_page,
                "Removing [[%s]]%s" % (self.fileName(), why),
            )

    def park(self):
        """
        This will do everything that is needed to park a closed candidate

        1. Check whether the count is verified or not
        2. If verified and featured:
          * Add page to 'Commons:Featured pictures, list'
          * Add to subpage of 'Commons:Featured pictures, list'
          * Add {{Assessments|featured=1}} or just the parameter if the template is already there
            to the picture page (should also handle subpages)
          * Add the picture to the 'Commons:Featured_pictures/chronological/current_month'
          * Add the template {{FPpromotion|File:XXXXX.jpg}} to the Talk Page of the nominator.
        3. If featured or not move it from 'Commons:Featured picture candidates/candidate list'
           to the log, f.ex. 'Commons:Featured picture candidates/Log/August 2009'
        """

        # First make a check that the page actually exist:
        if not self.page.exists():
            out("%s: (no such page?!)" % self.cutTitle())
            return

        # First look for verified results
        text = self.page.get(get_redirect=True)
        results = re.findall(self._VerifiedR, text)

        if not results:
            out("%s: (ignoring, no verified results)" % self.cutTitle())
            return

        if len(results) > 1:
            out("%s: (ignoring, several verified results ?)" % self.cutTitle())
            return

        if self.isWithdrawn():
            out("%s: (ignoring, was withdrawn)" % self.cutTitle())
            return

        if self.isFPX():
            out("%s: (ignoring, was FPXed)" % self.cutTitle())
            return

        # Check if the image page exist, if not we ignore this candidate
        if not pywikibot.Page(G_Site, self.fileName()).exists():
            out("%s: (WARNING: ignoring, can't find image page)" % self.cutTitle())
            return

        # Ok we should now have a candidate with verified results that we can park
        vres = results[0]

        # If the suffix to the title has not been added, add it now
        new_text = self.fixHeader(text, vres[3])
        if new_text != text:
            self.commit(text, new_text, self.page, "Fixed header")

        if vres[3] == "yes":
            self.handlePassedCandidate(vres)
        elif vres[3] == "no":
            # Non Featured picure
            self.moveToLog(self._conString)
        else:
            out(
                "%s: (ignoring, unknown verified feature status '%s')"
                % (self.cutTitle(), vres[3])
            )
            return

    def handlePassedCandidate(self, results):
        """Must be implemented by subclass (do the park procedure for passing candidate)."""
        raise NotImplementedException()

    @staticmethod
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
        pywikibot.showDiff(
            old_text,
            new_text,
            )

        if G_Dry:
            choice = "n"
        elif G_Auto:
            choice = "y"
        else:
            choice = pywikibot.bot.input_choice(
                "Do you want to accept these changes to '%s' with comment '%s' ?"
                % (page.title(), comment),
                [('yes', 'y'), ('no', 'n'), ('quit', 'q')],
            )

        if choice == "y":
            page.put(new_text, comment=comment, watchArticle=True, minorEdit=False)
        elif choice == "q":
            out("Aborting.")
            sys.exit(0)
        else:
            out("Changes to '%s' ignored" % page.title())


class FPCandidate(Candidate):
    """A candidate up for promotion."""

    def __init__(self, page):
        """Constructor."""
        Candidate.__init__(
            self,
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
            return (
                "\n\n{{FPC-results-unreviewed|support=%d|oppose=%d|neutral=%d|featured=%s|gallery=%s|sig=~~~~}}"
                % (self._pro, self._con, self._neu, "yes" if self.isPassed() else "no", self.findGalleryOfFile() )
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

        # Strip away any eventual section
        # as there is not implemented support for it
        fgallery = re.sub(r"#.*", "", results[4])

        # Now addToCategorizedFeaturedList can handle sections within the gallery page
        gallery_without_removing_section = results[4]

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

        # Featured picture
        if not len(fgallery):
            out("%s: (ignoring, gallery not defined)" % self.cutTitle())
            return
        self.addToFeaturedList(re.search(r"(.*?)(?:/|$)", fgallery).group(1))
        self.addToCategorizedFeaturedList(gallery_without_removing_section)
        self.addAssessments()
        self.addToCurrentMonth()
        self.notifyNominator()
        self.notifyUploader()
        self.moveToLog(self._proString)


class DelistCandidate(Candidate):
    """A delisting candidate."""

    def __init__(self, page):
        Candidate.__init__(
            self,
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

        references = self.getImagePage().getReferences(withTemplateInclusion=False)
        for ref in references:
            if ref.title().startswith("Commons:Featured pictures/"):
                if ref.title().startswith("Commons:Featured pictures/chronological"):
                    out("Adding delist note to %s" % ref.title())
                    old_text = ref.get(get_redirect=True)
                    now = datetime.datetime.utcnow()
                    new_text = re.sub(
                        r"(([Ff]ile|[Ii]mage):%s.*)\n"
                        % wikipattern(self.cleanTitle(keepExtension=True)),
                        r"\1 '''Delisted %d-%02d-%02d (%s-%s)'''\n"
                        % (now.year, now.month, now.day, results[1], results[0]),
                        old_text,
                    )
                    self.commit(
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
                    self.commit(
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

        self.commit(old_text, new_text, imagePage, "Delisted")


def wikipattern(s):
    """Return a string that can be matched against different way of writing it on wikimedia projects."""

    def rep(m):
        if m.group(0) == " " or m.group(0) == "_":
            return "[ _]"
        elif m.group(0) == "(" or m.group(0) == ")" or m.group(0) == "*" or m.group(0) == "+" or m.group(0) == "=" or m.group(0) == "?" or m.group(0) == "!" or m.group(0) == "^" or m.group(0) == "-":
            return "\\" + m.group(0)

    return re.sub(r"[ _()*+=?!^-]", rep, s)


def out(text, newline=True, date=False, color=None):
    """Just output some text to the consoloe or log."""
    if color:
        text = "\03{%s}%s\03{default}" % (color, text)
    dstr = (
        "%s: " % datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        if date and not G_LogNoTime
        else ""
    )
    pywikibot.stdout("%s%s" % (dstr, text), newline=newline)


def findCandidates(page_url, delist):
    """Finds all candidates on the main FPC page."""
    page = pywikibot.Page(G_Site, page_url)
    candidates = []
    templates = page.templates()
    for template in templates:
        title = template.title()
        if title.startswith(candPrefix):
            # out("Adding '%s' (delist=%s)" % (title,delist))
            if delist and "/removal/" in title:
                candidates.append(DelistCandidate(template))
            elif not delist and "/removal/" not in title:
                candidates.append(FPCandidate(template))
        else:
            pass
            # out("Skipping '%s'" % title)
    return candidates


def checkCandidates(check, page, delist):
    """
    Calls a function on each candidate found on the specified page

    @param check  A function in Candidate to call on each candidate
    @param page   A page containing all candidates
    @param delist Boolean, telling whether this is delistings of fpcs
    """
    if not G_Site.logged_in():
        G_Site.login()

    candidates = findCandidates(page, delist)

    def containsPattern(candidate):
        return candidate.cleanTitle().lower().find(G_MatchPattern.lower()) != -1

    candidates = list(filter(containsPattern, candidates))

    tot = len(candidates)
    i = 1
    for candidate in candidates:

        if not G_Threads:
            out("(%03d/%03d) " % (i, tot), newline=False, date=True)

        try:
            if G_Threads:
                while threading.activeCount() >= config.max_external_links:
                    time.sleep(0.1)
                thread = ThreadCheckCandidate(candidate, check)
                thread.start()
            else:
                check(candidate)
        except pywikibot.NoPage as error:
            out("No such page '%s'" % error, color="lightred")
        except pywikibot.LockedPage as error:
            out("Page is locked '%s'" % error, color="lightred")

        i += 1
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
    text = strip_tag(text, "nowiki")
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
        username = (data.user)
    if not history:
        return "Unknown"
    if link:
        return "[[User:%s|%s]]" % (username, username)
    else:
        return username

def converttostr(input_list, seperator):
   """Make string from list."""
   resultant_string = seperator.join(input_list)
   return resultant_string

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
    "[Oo]versaturated",
    "[Ss]trong oppose",
    "[Ww]eak oppose",
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

# Used to remove the prefix and just print the file names
# of the candidate titles.
candPrefix = "Commons:Featured picture candidates/"
PrefixR = re.compile("%s.*?([Ff]ile|[Ii]mage)?:" % candPrefix)

# Looks for result counts, an example of such a line is:
# '''result:''' 3 support, 2 oppose, 0 neutral => not featured.
#
PreviousResultR = re.compile(
    r"'''result:'''\s+(\d+)\s+support,\s+(\d+)\s+oppose,\s+(\d+)\s+neutral\s*=>\s*((?:not )?featured)",
    re.MULTILINE,
)

# Looks for verified results
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
CountedTemplateR = re.compile(
    r"^.*{{\s*FPC-results-unreviewed.*}}.*$\n?", re.MULTILINE
)
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


def main(*args):
    global G_Auto
    global G_Dry
    global G_Threads
    global G_LogNoTime
    global G_MatchPattern
    global G_Site

    # Will sys.exit(-1) if another instance is running
    me = singleton.SingleInstance()

    candidates_page = "Commons:Featured picture candidates/candidate_list"
    testLog = "Commons:Featured_picture_candidates/Log/January_2009"

    worked = False
    delist = False
    fpc = False

    # First look for arguments that should be set for all operations
    i = 1
    for arg in sys.argv[1:]:
        if arg == "-auto":
            G_Auto = True
            sys.argv.remove(arg)
            continue
        elif arg == "-dry":
            G_Dry = True
            sys.argv.remove(arg)
            continue
        elif arg == "-threads":
            G_Threads = True
            sys.argv.remove(arg)
            continue
        elif arg == "-delist":
            delist = True
            sys.argv.remove(arg)
            continue
        elif arg == "-fpc":
            fpc = True
            sys.argv.remove(arg)
            continue
        elif arg == "-notime":
            G_LogNoTime = True
            sys.argv.remove(arg)
            continue
        elif arg == "-match":
            if i + 1 < len(sys.argv):
                G_MatchPattern = sys.argv.pop(i + 1)
                sys.argv.remove(arg)
                continue
            else:
                out("Warning - '-match' need a pattern, aborting.", color="lightred")
                sys.exit(0)
        i += 1

    if not delist and not fpc:
        delist = True
        fpc = True

    # Can not use interactive mode with threads
    if G_Threads and (not G_Dry and not G_Auto):
        out("Warning - '-threads' must be run with '-dry' or '-auto'", color="lightred")
        sys.exit(0)

    args = pywikibot.handle_args(*args)
    G_Site = pywikibot.Site()

    # Abort on unknown arguments
    for arg in args:
        if arg not in [
            "-test",
            "-close",
            "-info",
            "-park",
            "-threads",
            "-fpc",
            "-delist",
            "-help",
            "-notime",
            "-match",
            "-auto",
        ]:
            out(
                "Warning - unknown argument '%s' aborting, see -help." % arg,
                color="lightred",
            )
            sys.exit(0)

    for arg in args:
        worked = True
        if arg == "-test":
            if delist:
                out("-test not supported for delisting candidates")
            if fpc:
                checkCandidates(Candidate.compareResultToCount, testLog, delist=False)
        elif arg == "-close":
            if delist:
                out("Closing delist candidates...", color="lightblue")
                checkCandidates(Candidate.closePage, candidates_page, delist=True)
            if fpc:
                out("Closing fpc candidates...", color="lightblue")
                checkCandidates(Candidate.closePage, candidates_page, delist=False)
        elif arg == "-info":
            if delist:
                out("Gathering info about delist candidates...", color="lightblue")
                checkCandidates(Candidate.printAllInfo, candidates_page, delist=True)
            if fpc:
                out("Gathering info about fpc candidates...", color="lightblue")
                checkCandidates(Candidate.printAllInfo, candidates_page, delist=False)
        elif arg == "-park":
            if G_Threads and G_Auto:
                out(
                    "Auto parking using threads is disabled for now...",
                    color="lightyellow",
                )
                sys.exit(0)
            if delist:
                out("Parking delist candidates...", color="lightblue")
                checkCandidates(Candidate.park, candidates_page, delist=True)
            if fpc:
                out("Parking fpc candidates...", color="lightblue")
                checkCandidates(Candidate.park, candidates_page, delist=False)

    if not worked:
        out("Warning - you need to specify an argument, see -help.", color="lightred")


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
