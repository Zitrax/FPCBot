# -*- coding: utf-8 -*-
"""
This bot runs as FPCBot on wikimedia commons
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

import wikipedia, re, datetime, sys, difflib

# Imports needed for threading
import threading, time, config

class NotImplementedException(Exception):
    """Not implemented"""

class ThreadCheckCandidate(threading.Thread):

    def __init__(self, candidate, check):
        threading.Thread.__init__(self)
        self.candidate = candidate
        self.check = check

    def run(self):
        self.check(self.candidate)


class Candidate():
    """
    This is one picture candidate

    This class just serves as base for the DelistCandidate and FPCandidate classes
    """

    def __init__(self, page, ProR, ConR, NeuR, SProR, SConR, SNeuR, ProString, ConString, ReviewedR, CountedR, VerifiedR ):
        """page is a wikipedia.Page object"""

        # Later perhaps this can be cleaned up by letting the subclasses keep the variables
        self.page          = page
        self._pro          = 0
        self._con          = 0
        self._neu          = 0
        self._proR         = ProR  # Regexp for positive votes
        self._conR         = ConR  # Regexp for negative votes
        self._neuR         = NeuR  # Regexp for neutral  votes
        self._s_proR       = SProR # Striked out positive regexp
        self._s_conR       = SConR # Striked out negative regexp
        self._s_neuR       = SNeuR # Striked out neutral  regexp
        self._proString    = ProString
        self._conString    = ConString
        self._ReviewedR    = ReviewedR
        self._CountedR     = CountedR
        self._VerifiedR    = VerifiedR
        self._votesCounted = False
        self._daysOld      = -1
        self._daysSinceLastEdit = -1
        self._creationTime = None
        self._striked      = None
        self._imgCount     = None
        self._fileName     = None
        self._listPageName = None

    def printAllInfo(self):
        """
        Console output of all information sought after
        """
        try:
            self.countVotes()
            out("%s: S:%02d(-%02d) O:%02d(-%02d) N:%02d(-%02d) D:%02d De:%02d Se:%d Im:%02d W:%s (%s)" % 
                             ( self.cutTitle(),
                               self._pro,self._striked[0],self._con,self._striked[1],
                               self._neu,self._striked[2],
                               self.daysOld(),self.daysSinceLastEdit(),self.sectionCount(),
                               self.imageCount(),self.isWithdrawn(),
                               self.statusString()))
        except wikipedia.NoPage:
            out("%s: -- No such page -- " % self.cutTitle(), color="lightred")


    def nominator(self,link=True):
        """Return the link to the user that nominated this candidate"""
        history = self.page.getVersionHistory(reverseOrder=True,revCount=1)
        if not history:
            return "Unknown"
        if link:
            return "[[User:%s|%s]]" % (history[0][2],history[0][2])
        else:
            return history[0][2]

    def uploader(self):
        """Return the link to the user that uploaded the nominated image"""
        page = wikipedia.Page(wikipedia.getSite(), self.fileName())
        history = page.getVersionHistory(reverseOrder=True,revCount=1)
        if not history:
            return "Unknown"
        return "[[User:%s|%s]]" % (history[0][2],history[0][2])        

    def creator(self):
        """Return the link to the user that created the image"""
        return self.uploader()

    def countVotes(self):
        """
        Counts all the votes for this nomnination
        and subtracts eventual striked out votes
        """

        if self._votesCounted:
            return

        text = self.page.get(get_redirect=True)
        self._pro = len(re.findall(self._proR,text)) 
        self._con = len(re.findall(self._conR,text))
        self._neu = len(re.findall(self._neuR,text))

        self.findStrikedOutVotes()
        self._pro -= self._striked[0]
        self._con -= self._striked[1]
        self._neu -= self._striked[2]

        self._votesCounted = True

    def findStrikedOutVotes(self):
        """
        We should not count striked out votes so 
        find them and reduce the counts.
        """
        
        if self._striked:
            return self._striked

        text  = self.page.get(get_redirect=True)
        s_pro = len(re.findall(self._s_proR,text))
        s_con = len(re.findall(self._s_conR,text))
        s_neu = len(re.findall(self._s_neuR,text))

        self._striked = (s_pro,s_con,s_neu)
        return self._striked
        

    def isWithdrawn(self):
        """Withdrawn nominations should not be counted"""
        text = self.page.get(get_redirect=True)
        withdrawn  = len(re.findall(WithdrawnR,text))
        withdrawn -= len(re.findall(StrikedOutWithdrawnR,text))
        return withdrawn>0

    def isFPX(self):
        """Page marked with FPX template"""
        return len(re.findall(FpxR,self.page.get(get_redirect=True)))

    def rulesOfFifthDay(self):
        """Check if any of the rules of the fifth day can be applied"""
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
            out("\"%s\" no such page?!" % self.cutTitle() )
            return

        if (self.isWithdrawn() or self.isFPX()) and self.imageCount() <= 1:
            # Will close withdrawn nominations if there is more than one 
            # full day since the last edit

            why = "withdrawn" if self.isWithdrawn() else "FPXed"

            oldEnough = self.daysSinceLastEdit() > 0
            out("\"%s\" %s %s" % (self.cutTitle(),why,"closing" if oldEnough else "but waiting a day"))

            if not oldEnough:
                return False

            self.moveToLog(why)
            return True

        fifthDay = self.rulesOfFifthDay()

        if not fifthDay and not self.isDone():
            out("\"%s\" is still active, ignoring" % self.cutTitle())
            return False

        old_text = self.page.get(get_redirect=True)

        if re.search(r'{{\s*FPC-closed-ignored.*}}',old_text):
            out("\"%s\" is marked as ignored, so ignoring" % self.cutTitle())
            return False            

        if self.imageCount() > 1:
            # Do not add the template until the full period is over
            if not self.isDone():
                out("\"%s\" is still active, ignoring" % self.cutTitle())
                return False
            out("\"%s\" contains multiple images, ignoring" % self.cutTitle())
            # Remove any existing FPC templates
            new_text = re.sub(self._ReviewedR,'',old_text)
            new_text = re.sub(self._CountedR,'',new_text)
            not_corrected = new_text == old_text
            new_text = new_text + "\n\n{{FPC-closed-ignored|multiple images}}\n/~~~~"
            self.commit(old_text,new_text,self.page,"Marking as ignored" if not_corrected else "Marking as ignored (needs to be closed according to the manual instructions)")
            return False

        if re.search(self._CountedR,old_text):
            out("\"%s\" needs review, ignoring" % self.cutTitle())
            return False            

        if re.search(self._ReviewedR,old_text):
            out("\"%s\" already closed and reviewed, ignoring" % self.cutTitle())
            return False            

        self.countVotes()

        result = self.getResultString()
            
        new_text = old_text + result
        
        # Add the featured status to the header
        new_text = re.sub(r'(===.*)(===)',r"\1%s\2" %  (", %s" % self._proString if self.isPassed() else ", %s" % self._conString), new_text)

        self.commit(old_text,new_text,self.page,self.getCloseCommitComment() + (" (FifthDay=%s)" % ("yes" if fifthDay else "no")) )
        
        return True

    def getResultString(self):
        """Must be implemented by the subclasses (Text to add to closed pages)"""
        raise NotImplementedException()
        
    def getCloseCommitComment(self):
        """Must be implemened by the subclasses (Commit comment for closed pages)"""
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

        history = self.page.getVersionHistory(reverseOrder=True,revCount=1)
        if not history:
            out("Could not retrieve history for '%s', returning now()" % self.page.title())
            return datetime.datetime.now()

        m = re.match(DateR,history[0][1].lower())
        self._creationTime = datetime.datetime(int(m.group(5)),
                                               Month[m.group(4)],
                                               int(m.group(3)),
                                               int(m.group(1)),
                                               int(m.group(2)))

        #print "C:" + self._creationTime.isoformat()
        #print "N:" + datetime.datetime.utcnow().isoformat()
        return self._creationTime
        

    def statusString(self):
        """Short status string about the candidate"""
        if self.isIgnored():
            return "Ignored"
        elif self.isWithdrawn():
            return "Withdrawn"
        elif not self.isDone():
            return "Active"
        else:
            return self._proString if self.isPassed() else self._conString

    def daysOld(self):
        """Find the number of days this nomination has existed"""

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
            lastEdit = datetime.datetime.strptime(self.page.editTime(),"%Y%m%d%H%M%S")
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

        return self._pro >= 5 and \
            (self._pro >= 2*self._con)
    

    def isIgnored(self):
        """Some nominations currently require manual check"""
        return self.imageCount() > 1

    def sectionCount(self):
        """Count the number of sections in this candidate"""
        text = self.page.get(get_redirect=True)
        return len(re.findall(SectionR,text))

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
        for m in re.finditer(ImagesR,text):
            matches.append(m)

        count = len(matches)

        if count >= 2:
            # We have several images, check if they are too small to be counted
            for img in matches:
                s = re.search(ImagesSizeR,img.group(0))
                if s:
                    if int(s.group(1)) < 150:
                        count -= 1
                    else: continue

                if re.search(ImagesThumbR,img.group(0)):
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
        return re.findall(PreviousResultR,text)

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
        was_featured = (old_res[3] == u'featured')
        ws = int(old_res[0])
        wo = int(old_res[1])
        wn = int(old_res[2])
        self.countVotes()

        if self._pro == ws and self._con == wo and self._neu == wn and was_featured == self.isPassed():
            status = "OK"
        else:
            status = "FAIL"

        # List info to console
        out("%s: S%02d/%02d O:%02d/%02d N%02d/%02d F%d/%d (%s)" % (self.cutTitle(),
                                                                                self._pro,ws,
                                                                                self._con ,wo,
                                                                                self._neu,wn,
                                                                                self.isPassed(),was_featured,
                                                                                status))

    def cutTitle(self):
        """Returns a fixed with title"""
        return re.sub(PrefixR,'',self.page.title())[0:50].ljust(50)

    def cleanTitle(self,keepExtension=False):
        """Returns a title string without prefix and extension"""
        noprefix =  re.sub(PrefixR,'',self.page.title())
        if keepExtension:
            return noprefix
        else:
            return re.sub(r'\.\w{1,3}$\s*','',noprefix)

    def fileName(self):
        """
        Return only the filename of this candidate
        This is first based on the title of the page but if that page is not found
        then the first image link in the page is used.
        """
        # The regexp here also removes any possible crap between the prefix
        # and the actual start of the filename.
        if self._fileName:
            return self._fileName

        self._fileName = re.sub("(%s.*?)([Ff]ile|[Ii]mage)" % candPrefix,r'\2',self.page.title())

        if not wikipedia.Page(wikipedia.getSite(), self._fileName).exists():
            match = re.search(ImagesR,self.page.get(get_redirect=True))
            if match: self._fileName = match.group(1)

        return self._fileName


    def addToFeaturedList(self,category):
        """
        Will add this page to the list of featured images.
        This uses just the base of the category, like 'Animals'.
        Should only be called on closed and verified candidates
        
        This is ==STEP 1== of the parking procedure

        @param category The categorization category
        """        


        listpage = 'Commons:Featured pictures, list'
        page = wikipedia.Page(wikipedia.getSite(), listpage)
        old_text = page.get(get_redirect=True)
        
        # First check if we are already on the page,
        # in that case skip. Can happen if the process
        # have been previously interrupted.
        if re.search(wikipattern(self.fileName()),old_text):
            out("Skipping addToFeaturedList for '%s', page already listed." % self.cleanTitle())
            return

        # This function first needs to find the main category
        # then inside the gallery tags remove the last line and
        # add this candidate to the top

        # Thanks KODOS for a nice regexp gui
        # This adds ourself first in the list of length 4 and removes the last
        # all in the chosen category
        ListPageR = re.compile(r"(^==\s*{{{\s*\d+\s*\|%s\s*}}}\s*==\s*<gallery.*>\s*)(.*\s*)(.*\s*.*\s*)(.*\s*)(</gallery>)" % wikipattern(category), re.MULTILINE)
        new_text = re.sub(ListPageR,r"\1%s\n\2\3\5" % self.fileName(), old_text)
        self.commit(old_text,new_text,page,"Added [[%s]]" % self.fileName() )

    def addToCategorizedFeaturedList(self,category):
        """
        Adds the candidate to the page with categorized featured
        pictures. This is the full category.

        This is ==STEP 2== of the parking procedure

        @param category The categorization category
        """
        catpage = "Commons:Featured pictures/" + category
        page = wikipedia.Page(wikipedia.getSite(), catpage)
        old_text = page.get(get_redirect=True)

        # First check if we are already on the page,
        # in that case skip. Can happen if the process
        # have been previously interrupted.
        if re.search(wikipattern(self.fileName()),old_text):
            out("Skipping addToCategorizedFeaturedList for '%s', page already listed." % self.cleanTitle())
            return

        # A few categories are treated specially, the rest is appended to the last gallery
        if category == "Places/Panoramas":
            new_text = re.sub(LastImageR,r'\1\n[[%s|thumb|627px|left|%s]]' % (self.fileName(),self.cleanTitle()) , old_text, 1)
        elif category == "Animated":
            new_text = re.sub(LastImageR,r'\1\n[[%s|frame|left|%s]]' % (self.fileName(),self.cleanTitle()) , old_text, 1)
        else:
            # We just need to append to the bottom of the gallery with an added title
            # The regexp uses negative lookahead such that we place the candidate in the
            # last gallery on the page.
            new_text = re.sub('(?s)</gallery>(?!.*</gallery>)',"%s|%s\n</gallery>" % (self.fileName(),self.cleanTitle()) , old_text, 1)

        self.commit(old_text,new_text,page,"Added [[%s]]" % self.fileName());

    def getImagePage(self):
        """Get the image page itself"""
        return wikipedia.Page(wikipedia.getSite(), self.fileName())

    def addAssessments(self):
        """
        Adds the the assessments template to a featured
        pictures descripion page.

        This is ==STEP 3== of the parking procedure

        """
        page = self.getImagePage()
        old_text = page.get(get_redirect=True)
        
        AssR = re.compile(r'{{\s*[Aa]ssessments\s*\|(.*)}}')

        # First check if there already is an assessments template on the page
        params = re.search(AssR,old_text)
        if params:
            # Make sure to remove any existing com param
            params = re.sub(r"\|\s*com\s*=\s*\d+\|?",'',params.group(1))
            params += "|com=1"
            new_ass = "{{Assessments|%s}}" % params
            new_text = re.sub(AssR,new_ass,old_text)
            if new_text == old_text:
                out("No change in addAssessments, '%s' already featured." % self.cleanTitle())
                return
        else:
            # There is no assessments template so just add it
            end = findEndOfTemplate(old_text,"[Ii]nformation")
            new_text = old_text[:end] + "\n{{Assessments|com=1}}\n" + old_text[end:]
            #new_text = re.sub(r'({{\s*[Ii]nformation)',r'{{Assessments|com=1}}\n\1',old_text)

        self.commit(old_text,new_text,page,"FPC promotion")

    def addToCurrentMonth(self):
        """
        Adds the candidate to the list of featured picture this month

        This is ==STEP 4== of the parking procedure
        """
        monthpage = 'Commons:Featured_pictures/chronological/current_month'
        page = wikipedia.Page(wikipedia.getSite(), monthpage)
        old_text = page.get(get_redirect=True)

        # First check if we are already on the page,
        # in that case skip. Can happen if the process
        # have been previously interrupted.
        if re.search(wikipattern(self.fileName()),old_text):
            out("Skipping addToCurrentMonth for '%s', page already listed." % self.cleanTitle())
            return

        #Find the number of lines in the gallery
        m = re.search(r"(?ms)<gallery>(.*)</gallery>",old_text)
        count = m.group(0).count("\n")

        # We just need to append to the bottom of the gallery
        # with an added title
        # TODO: We lack a good way to find the creator, so it is left out at the moment
        new_text = re.sub('</gallery>',"%s|%d '''%s''' <br> uploaded by %s, nominated by %s\n</gallery>" % 
                          (self.fileName(), count, self.cleanTitle(), self.uploader(), self.nominator()) , old_text)
        self.commit(old_text,new_text,page,"Added [[%s]]" % self.fileName() );
        
    def notifyNominator(self):
        """
        Add a template to the nominators talk page

        This is ==STEP 5== of the parking procedure
        """
        talk_link = "User_talk:%s" % self.nominator(link=False)
        talk_page = wikipedia.Page(wikipedia.getSite(), talk_link)

        try:
            old_text = talk_page.get(get_redirect=True)
        except wikipedia.NoPage:
            out("notifyNominator: No such page '%s' but ignoring..." % talk_link, color="lightred")
            return

        # First check if we are already on the page,
        # in that case skip. Can happen if the process
        # have been previously interrupted.
        if re.search("{{FPpromotion\|%s}}" % wikipattern(self.fileName()),old_text):
            out("Skipping notifyNominator for '%s', page already listed at '%s'." % (self.cleanTitle(),talk_link))
            return

        new_text = old_text + "\n\n== FP Promotion ==\n{{FPpromotion|%s}} /~~~~" % self.fileName()
        self.commit(old_text,new_text,talk_page,"FPC promotion of [[%s]]" % self.fileName() )

    def moveToLog(self,reason=None):
        """
        Remove this candidate from the current list 
        and add it to the log of the current month

        This is ==STEP 6== of the parking procedure
        """

        why = (" (%s)" % reason) if reason else ""
        
        # Add to log
        # (Note FIXME, we must probably create this page if it does not exist)
        today = datetime.date.today()
        current_month = Month2[today.month]
        log_link = "Commons:Featured picture candidates/Log/%s %s" % (current_month,today.year)
        log_page = wikipedia.Page(wikipedia.getSite(), log_link)
        old_log_text = log_page.get(get_redirect=True)

        if re.search(wikipattern(self.fileName()),old_log_text):
            out("Skipping add in moveToLog for '%s', page already there" % self.cleanTitle())
        else:
            new_log_text = old_log_text + "\n{{%s}}" % self.page.title()
            self.commit(old_log_text,new_log_text,log_page,"Adding [[%s]]%s" % (self.fileName(),why) )

        # Remove from current list
        candidate_page = wikipedia.Page(wikipedia.getSite(), self._listPageName)
        old_cand_text = candidate_page.get(get_redirect=True)
        new_cand_text = re.sub(r"{{\s*%s\s*}}.*?\n" % wikipattern(self.page.title()),'', old_cand_text)

        if old_cand_text == new_cand_text:
            wikipattern.output("Skipping remove in moveToLog for '%s', no change." % self.cleanTitle())
        else:
            self.commit(old_cand_text,new_cand_text,candidate_page,"Removing [[%s]]%s" % (self.fileName(),why) )

    def park(self):
        """
        This will do everything that is needed to park a closed candidate

        1. Check whether the count is verified or not
        2. If verified and featured:
          * Add page to 'Commons:Featured pictures, list'
          * Add to subpage of 'Commons:Featured pictures, list'
          * Add {{Assessments|com=1}} or just the parameter if the template is already there 
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
        results = re.findall(self._VerifiedR,text)
        
        if self.imageCount() > 1:
            out("%s: (ignoring, is multiimage)" % self.cutTitle())
            return

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
        if not wikipedia.Page(wikipedia.getSite(), self.fileName()).exists():
            out("%s: (WARNING: ignoring, can't find image page)" % self.cutTitle())
            return

        # Ok we should now have a candidate with verified results that we can park
        vres = results[0]
        if vres[3] == "yes":
            self.handlePassedCandidate(vres)
        elif  vres[3] == "no":
            # Non Featured picure
            self.moveToLog(self._conString)
        else:
            out("%s: (ignoring, unknown verified feature status '%s')" % (self.cutTitle(),vres[3]))
            return

        
    def handlePassedCandidate(self,results):
        """Must be implemented by subclass (do the park procedure for passing candidate)"""
        raise NotImplementedException()

    def commit(self,old_text,new_text,page,comment):
        """
        This will commit new_text to the page
        and unless running in automatic mode it
        will show you the diff and ask you to accept it.

        @param old_text Used to show the diff
        @param new_text Text to be submitted as the new page
        @param page Page to submit the new text to
        @param comment The edit comment
        """

        # Show the diff
        for line in difflib.context_diff(old_text.splitlines(1), new_text.splitlines(1)):
            if line.startswith('+ '):
                out(line,newline=False, color="lightgreen")
            elif line.startswith('- '):
                out(line,newline=False, color="lightred")
            elif line.startswith('! '):
                out(line,newline=False, color="lightyellow")
            else:
                out(line,newline=False)
        out("\n")

        if G_Dry:
            choice = 'n'
        elif G_Auto:
            choice = 'y'
        else:
            choice = wikipedia.inputChoice(
                u"Do you want to accept these changes to '%s' with comment '%s' ?" % ( page.title(), comment) ,
                ['Yes', 'No', "Quit"],
                ['y', 'N', 'q'], 'N')
        
        if choice == 'y':
            page.put(new_text, comment=comment, watchArticle=True, minorEdit=False, maxTries=10 );
        elif choice == 'q':
            out("Aborting.")
            sys.exit(0)
        else:
            out("Changes to '%s' ignored" % page.title())
        

class FPCandidate(Candidate):
    """A candidate up for promotion"""

    def __init__(self, page):
        Candidate.__init__(self,page,SupportR,OpposeR,NeutralR,StrikedOutSupportR,StrikedOutOpposeR,StrikedOutNeutralR,"featured","not featured",ReviewedTemplateR,CountedTemplateR,VerifiedResultR)
        self._listPageName = "Commons:Featured picture candidates/candidate list"

    def getResultString(self):
        return "\n\n{{FPC-results-ready-for-review|support=%d|oppose=%d|neutral=%d|featured=%s|category=|sig=~~~~}}" % \
            (self._pro,self._con,self._neu,"yes" if self.isPassed() else "no")

    def getCloseCommitComment(self):
        return "Closing for review (%d support, %d oppose, %d neutral, featured=%s)" % (self._pro,self._con,self._neu,"yes" if self.isPassed() else "no")

    def handlePassedCandidate(self,results):
        
        # Strip away any eventual section
        # as there is not implemented support for it
        fcategory = re.sub(r'#.*','',results[4])
        
        # Featured picture
        if not len(fcategory):
            out("%s: (ignoring, category not set)" % self.cutTitle())
            return
        self.addToFeaturedList(re.search(r'(.*?)(?:/|$)',fcategory).group(1))
        self.addToCategorizedFeaturedList(fcategory)
        self.addAssessments()
        self.addToCurrentMonth()
        self.notifyNominator()
        self.moveToLog(self._proString)

class DelistCandidate(Candidate):
    """A delisting candidate"""

    def __init__(self, page):
        Candidate.__init__(self,page,DelistR,KeepR,NeutralR,StrikedOutDelistR,StrikedOutKeepR,StrikedOutNeutralR,"delisted","not delisted",DelistReviewedTemplateR,DelistCountedTemplateR,VerifiedDelistResultR)
        self._listPageName = "Commons:Featured picture candidates/removal"

    def getResultString(self):
        return "\n\n{{FPC-delist-results-ready-for-review|delist=%d|keep=%d|neutral=%d|delisted=%s|sig=~~~~}}" % \
            (self._pro,self._con,self._neu,"yes" if self.isPassed() else "no")

    def getCloseCommitComment(self):
        return "Closing for review (%d delist, %d keep, %d neutral, delisted=%s)" % (self._pro,self._con,self._neu,"yes" if self.isPassed() else "no")

    def handlePassedCandidate(self,results):
        # Delistings does not care about the category
        self.removeFromFeaturedLists(results)
        self.removeAssessments()
        self.moveToLog(self._proString)

    def removeFromFeaturedLists(self,results):
        """Remove a candidate from all featured lists"""
        
        # We skip checking the page with the 4 newest images
        # the chance that we are there is very small and evenf
        # if we are we will soon be rotated away anyway.
        # So check and remove the candidate from any category pages
        
        references = self.getImagePage().getReferences(withTemplateInclusion=False)
        for ref in references:
            if ref.title().startswith("Commons:Featured pictures/"):
                if ref.title().startswith("Commons:Featured pictures/chronological"):
                    out("Adding delist note to %s" % ref.title())
                    old_text = ref.get(get_redirect=True)
                    now = datetime.datetime.utcnow()
                    new_text = re.sub(r"(([Ff]ile|[Ii]mage):%s.*)\n" % wikipattern(self.cleanTitle(keepExtension=True)),r"\1 '''Delisted %d-%02d-%02d (%s-%s)'''\n" % (now.year,now.month,now.day,results[1],results[0]), old_text)
                    self.commit(old_text,new_text,ref,"Delisted [[%s]]" % self.fileName() )
                else:
                    old_text = ref.get(get_redirect=True)
                    new_text = re.sub(r"([[)?([Ff]ile|[Ii]mage):%s.*\n" % wikipattern(self.cleanTitle(keepExtension=True)),'', old_text)
                    self.commit(old_text,new_text,ref,"Removing [[%s]]" % self.fileName() )

    def removeAssessments(self):
        """Remove FP status from an image"""
        
        imagePage = self.getImagePage()
        old_text = imagePage.get(get_redirect=True)

        # First check for the old {{Featured picture}} template
        new_text = re.sub(r'{{[Ff]eatured[ _]picture}}','{{Delisted picture}}',old_text)

        # Then check for the assessments template
        # The replacement string needs to use the octal value for the char '2' to
        # not confuse python as '\12\2' would obviously not work
        new_text = re.sub(r'({{[Aa]ssessments\s*\|.*com\s*=\s*)1(.*?}})',r'\1\062\2',new_text)

        self.commit(old_text,new_text,imagePage,"Delisted")


def wikipattern(s):
    """Return a string that can be matched against different way of writing it on wikimedia projects"""
    def rep(m):
        if m.group(0) == ' ' or m.group(0) == '_':
            return "[ _]";
        elif m.group(0) == '(' or m.group(0) == ')':
            return '\\' + m.group(0)
        
    return re.sub('[ _\()]',rep,s)

def out(text, newline=True, date=False, color=None):
    """Just output some text to the consoloe or log"""
    if color:
        text = "\03{%s}%s\03{default}" % (color, text)
    dstr = "%s: " % datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") if date and not G_LogNoTime else ""
    wikipedia.output("%s%s" % (dstr,text) , toStdout=True, newline=newline)

def findCandidates(page_url, delist):
    """This finds all candidates on the main FPC page"""

    page = wikipedia.Page(wikipedia.getSite(), page_url)

    candidates = []
    templates = page.getTemplates()
    for template in templates:
        title = template.title()
        if title.startswith(candPrefix):
            # out("Adding '%s' (delist=%s)" % (title,delist))
            if delist:
                candidates.append(DelistCandidate(template))
            else:
                candidates.append(FPCandidate(template))
        else:
            pass
            #out("Skipping '%s'" % title)
    return candidates

def checkCandidates(check,page,delist):
    """
    Calls a function on each candidate found on the specified page

    @param check  A function in Candidate to call on each candidate
    @param page   A page containing all candidates
    @param delist Boolean, telling whether this is delistings of fpcs
    """
    candidates = findCandidates(page,delist)

    def containsPattern(candidate):
        return candidate.cleanTitle().lower().find(G_MatchPattern) != -1

    candidates = filter(containsPattern,candidates)

    tot = len(candidates)
    i = 1
    for candidate in candidates:

        if not G_Threads:
            out("(%03d/%03d) " %(i,tot), newline=False, date=True)

        try:
            if G_Threads:
                while threading.activeCount() >= config.max_external_links:
                    time.sleep(0.1)
                thread = ThreadCheckCandidate(candidate,check)
                thread.start()
            else:
                check(candidate)
        except wikipedia.NoPage, error:
            out("No such page '%s'" % error, color="lightred")
        except wikipedia.LockedPage:
            out("Page is locked '%s'" % candidate.cleanTitle(), color="lightred")

        i += 1

def findEndOfTemplate(text,template):
    """
    As regexps can't properly deal with nested parantheses this
    function will manually scan for where a template ends
    such that we can insert new text after it.
    Will return the position or 0 if not found.
    """
    m = re.search(r"{{\s*%s" % template,text) 
    if not m:
        return 0
    
    lvl = 0
    cp = m.start()+2

    while cp < len(text):
        ns = text.find("{{",cp)
        ne = text.find("}}",cp)

        # If we see no end tag, we give up
        if ne==-1: 
            return 0

        # Handle case when there are no more start tags
        if ns==-1:
            if not lvl:
                return ne+2
            else:
                lvl -= 1
                cp = ne+2

        elif not lvl and ne < ns:
            return ne+2
        elif ne < ns:
            lvl -= 1
            cp = ne+2 
        else:
            lvl += 1
            cp = ns+2
    # Apparently we never found it
    return 0
    
# Data and regexps used by the bot
Month  = { 'january':1, 'february':2, 'march':3, 'april':4, 'may':5, 'june':6, 'july':7, 'august':8, 'september':9, 'october':10, 'november':11, 'december':12 }
Month2  = { 1:'January', 2:'February', 3:'March', 4:'April', 5:'May', 6:'June', 7:'July', 8:'August', 9:'September', 10:'October', 11:'November', 12:'December' }
DateR = re.compile('(\d\d):(\d\d), (\d{1,2}) ([a-z]+) (\d{4})')

# List of valid templates
# They are taken from the page Commons:Polling_templates and some common redirects
support_templates = (u'[Ss]upport',u'[Pp]ro',u'[Ss]im',u'[Tt]ak',u'[Ss]í',u'[Pp]RO',u'[Ss]up',u'[Yy]es',u'[Oo]ui',u'[Kk]yllä', # First support + redirects
                     u'падтрымліваю',u'[Pp]our',u'[Tt]acaíocht',u'[Cc]oncordo',u'דעב', 
                     u'[Ss]amþykkt',u'支持',u'찬성',u'[Ss]for',u'за',u'[Ss]tödjer',u'เห็นด้วย',u'[Dd]estek',
                     u'[Aa] favore?',u'[Ss]trong support',u'')
oppose_templates  = (u'[Oo]ppose',u'[Kk]ontra',u'[Nn]ão',u'[Nn]ie',u'[Mm]autohe',u'[Oo]pp',u'[Nn]ein',u'[Ee]i', # First oppose + redirect
                     u'[Cс]упраць',u'[Ee]n contra',u'[Cc]ontre',u'[Ii] gcoinne',u'[Dd]íliostaigh',u'[Dd]iscordo',u'נגד',u'á móti',u'反対',u'除外',u'반대',
                     u'[Mm]ot',u'против',u'[Ss]tödjer ej',u'ไม่เห็นด้วย',u'[Kk]arsi',u'FPX contested',u'[Cc]ontra',u'[Cc]ontrario')
neutral_templates = (u'[Nn]eutral?',u'[Oo]partisk',u'[Nn]eutre',u'[Nn]eutro',u'נמנע',u'[Nn]øytral',u'中立',u'Нэўтральна',u'[Tt]arafsız',u'Воздерживаюсь',
                     u'[Hh]lutlaus',u'중립',u'[Nn]eodrach',u'เป็นกลาง','[Vv]n')
delist_templates  = (u'[Dd]elist',u'sdf') # Should the remove templates be valid here ? There seem to be no internationalized delist versions
keep_templates    = (u'[Kk]eep',u'[Vv]k',u'[Mm]antener',u'[Gg]arder',u'維持',u'[Bb]ehold',u'[Mm]anter',u'[Bb]ehåll',u'เก็บ',u'保留')

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
PreviousResultR = re.compile('\'\'\'result:\'\'\'\s+(\d+)\s+support,\s+(\d+)\s+oppose,\s+(\d+)\s+neutral\s*=>\s*((?:not )?featured)',re.MULTILINE)

# Looks for verified results
VerifiedResultR = re.compile(r'{{\s*FPC-results-reviewed\s*\|\s*support\s*=\s*(\d+)\s*\|\s*oppose\s*=\s*(\d+)\s*\|\s*neutral\s*=\s*(\d+)\s*\|\s*featured\s*=\s*(\w+)\s*\|\s*category\s*=\s*([^|]*).*}}',re.MULTILINE)
VerifiedDelistResultR = re.compile(r'{{\s*FPC-delist-results-reviewed\s*\|\s*delist\s*=\s*(\d+)\s*\|\s*keep\s*=\s*(\d+)\s*\|\s*neutral\s*=\s*(\d+)\s*\|\s*delisted\s*=\s*(\w+).*?}}',re.MULTILINE)

# Matches the entire line including newline so they can be stripped away
CountedTemplateR        = re.compile(r'^.*{{\s*FPC-results-ready-for-review.*}}.*$\n?',re.MULTILINE)
DelistCountedTemplateR  = re.compile(r'^.*{{\s*FPC-delist-results-ready-for-review.*}}.*$\n?',re.MULTILINE)
ReviewedTemplateR       = re.compile(r'^.*{{\s*FPC-results-reviewed.*}}.*$\n?',re.MULTILINE)
DelistReviewedTemplateR = re.compile(r'^.*{{\s*FPC-delist-results-reviewed.*}}.*$\n?',re.MULTILINE)

# Is whitespace allowed at the end ?
SectionR = re.compile('^={1,4}.+={1,4}\s*$',re.MULTILINE)
# Voting templates
SupportR = re.compile("{{\s*(?:%s)(\|.*)?\s*}}" % "|".join(support_templates),re.MULTILINE)
OpposeR  = re.compile("{{\s*(?:%s)(\|.*)?\s*}}" % "|".join( oppose_templates),re.MULTILINE)
NeutralR = re.compile("{{\s*(?:%s)(\|.*)?\s*}}" % "|".join(neutral_templates),re.MULTILINE)
DelistR  = re.compile("{{\s*(?:%s)(\|.*)?\s*}}" % "|".join( delist_templates),re.MULTILINE)
KeepR    = re.compile("{{\s*(?:%s)(\|.*)?\s*}}" % "|".join(   keep_templates),re.MULTILINE)
# Striked out votes 
StrikedOutSupportR = re.compile("<s>.*{{\s*(?:%s)(\|.*)?\s*}}.*</s>" % "|".join(support_templates),re.MULTILINE)
StrikedOutOpposeR  = re.compile('<s>.*{{\s*(?:%s)(\|.*)?\s*}}.*</s>' % "|".join( oppose_templates),re.MULTILINE)
StrikedOutNeutralR = re.compile('<s>.*{{\s*(?:%s)(\|.*)?\s*}}.*</s>' % "|".join(neutral_templates),re.MULTILINE)
StrikedOutDelistR  = re.compile('<s>.*{{\s*(?:%s)(\|.*)?\s*}}.*</s>' % "|".join( delist_templates),re.MULTILINE)
StrikedOutKeepR    = re.compile('<s>.*{{\s*(?:%s)(\|.*)?\s*}}.*</s>' % "|".join(   keep_templates),re.MULTILINE)
# Finds if a withdraw template is used
# This template has an optional string which we
# must be able to detect after the pipe symbol
WithdrawnR = re.compile('{{\s*[wW]ithdraw\s*(\|.*)?}}',re.MULTILINE)
# The </s> should match both inside and outside the ending }}
StrikedOutWithdrawnR = re.compile('<s>.*{{\s*[wW]ithdraw\s*(\|.*</s>.*}}|(\|.*)?}}.*</s>)',re.MULTILINE) 
# Nomination that contain the fpx template
FpxR = re.compile('{{\s*FPX(\|.*)?}}',re.MULTILINE)
# Counts the number of displayed images
ImagesR = re.compile('\[\[((?:[Ff]ile|[Ii]mage):[^\|]+).*?\]\]')
# Look for a size specification of the image link
ImagesSizeR = re.compile(r'\|.*?(\d+)\s*px')
# Find if there is a thumb parameter specified
ImagesThumbR = re.compile(r'\|\s*thumb\b')
# Finds the last image link on a page
LastImageR = re.compile(r'(?s)(\[\[(?:[Ff]ile|[Ii]mage):[^\n]*\]\])(?!.*\[\[(?:[Ff]ile|[Ii]mage):)')

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

def main(*args):

    fpcPage    = 'Commons:Featured picture candidates/candidate list'
    delistPage = 'Commons:Featured_picture_candidates/removal'
    testLog    = 'Commons:Featured_picture_candidates/Log/January_2009'

    worked = False
    delist = False
    fpc    = False
    global G_Auto
    global G_Dry
    global G_Threads
    global G_LogNoTime
    global G_MatchPattern

    # First look for arguments that should be set for all operations
    i = 1
    for arg in sys.argv[1:]:
        if arg == '-auto':
            G_Auto = True
            sys.argv.remove(arg)
        elif arg == '-dry':
            G_Dry = True
            sys.argv.remove(arg)
        elif arg == '-threads':
            G_Threads = True
        elif arg == '-delist':
            delist = True
        elif arg == '-fpc':
            fpc = True
        elif arg == '-notime':
            G_LogNoTime = True
        elif arg == '-match':
            if i+1 < len(sys.argv):
                G_MatchPattern = sys.argv.pop(i+1)
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

    args = wikipedia.handleArgs(*args)

    # Abort on unknown arguments
    for arg in args:
        if arg != '-test' and arg != '-close' and arg != '-info' and arg != '-park' and arg != '-threads' and arg != '-fpc' and arg != '-delist' and arg != '-help' and arg != '-notime' and arg != '-match':
            out("Warning - unknown argument '%s' aborting, see -help." % arg, color="lightred")
            sys.exit(0)            

    for arg in args:
        worked = True
        if arg == '-test':
            if delist:
                out("-test not supported for delisting candidates")
            if fpc:
                checkCandidates(Candidate.compareResultToCount,testLog,delist=False)
        elif arg == '-close':
            if delist:
                out("Closing delist candidates...", color="lightblue")
                checkCandidates(Candidate.closePage,delistPage,delist=True);
            if fpc:
                out("Closing fpc candidates...", color="lightblue")
                checkCandidates(Candidate.closePage,fpcPage,delist=False);
        elif arg == '-info':
            if delist:
                out("Gathering info about delist candidates...", color="lightblue")
                checkCandidates(Candidate.printAllInfo,delistPage,delist=True);
            if fpc:
                out("Gathering info about fpc candidates...", color="lightblue")
                checkCandidates(Candidate.printAllInfo,fpcPage,delist=False);
        elif arg == '-park':
            if G_Threads and G_Auto:
                out("Auto parking using threads is disabled for now...", color="lightyellow")
                sys.exit(0)
            if delist:
                out("Parking delist candidates...", color="lightblue")
                checkCandidates(Candidate.park,delistPage,delist=True);
            if fpc:
                out("Parking fpc candidates...", color="lightblue")
                checkCandidates(Candidate.park,fpcPage,delist=False);

    if not worked:
        out("Warning - you need to specify an argument, see -help.", color="lightred")
            

if __name__ == "__main__":
    try:
        main()
    finally:
        wikipedia.stopme()

