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

"""

# TODO: catch exceptions

import wikipedia, re, datetime, sys, difflib

candPrefix = "Commons:Featured picture candidates/"


class Candidate():
    """
    This is one feature picture candidate.

    TODO:
    * How to detect edits (multi image nomination) ?
      imagelinks() is no good it, there might be links that are not nominations

    """

    def __init__(self, page):
        """page is a wikipedia.Page object"""
        self.page          = page
        self._oppose       = 0
        self._support      = 0
        self._neutral      = 0
        self._unknown      = 0
        self._votesCounted = False
        self._featured     = False
        self._daysOld      = -1
        self._creationTime = None
        self._striked      = None

    def printAllInfo(self):
        """
        Console output of all information sought after
        """
        self.countVotes()
        wikipedia.output("%s: S:%02d(-%02d) O:%02d(-%02d) N:%02d U:%02d D:%02d Se:%d Im:%02d W:%s (%s)" % 
                         ( self.cutTitle(),
                           self._support,self._striked[0],self._oppose,self._striked[1],
                           self._neutral,self._unknown,
                           self.daysOld(),self.sectionCount(),
                           self.imageCount(),self.isWithdrawn(),
                           self.statusString()),
                         toStdout = True)


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

        text = self.page.get()
        self._support = len(re.findall(SupportR,text)) 
        self._oppose  = len(re.findall(OpposeR,text))
        self._neutral = len(re.findall(NeutralR,text))

        self.findStrikedOutVotes()
        self._support -= self._striked[0]
        self._oppose  -= self._striked[1]
        self._neutral -= self._striked[2]

        self._votesCounted = True

    def findStrikedOutVotes(self):
        """
        We should not count striked out votes so 
        find them and reduce the counts.
        """
        
        if self._striked:
            return self._striked

        text = self.page.get()
        s_support = len(re.findall(StrikedOutSupportR,text))
        s_oppose  = len(re.findall(StrikedOutOpposeR,text))
        s_neutral = len(re.findall(StrikedOutNeutralR,text))

        self._striked = (s_support,s_oppose,s_neutral)
        return self._striked
        

    def isWithdrawn(self):
        """Withdrawn nominations should not be counted"""
        return len(re.findall(WithdrawnR,self.page.get()))

    def isFPX(self):
        """Page marked with FPX template"""
        return len(re.findall(FpxR,self.page.get()))

    def closePage(self):
        """
        Will add the voting results to the page if it is finished.
        If it was, True is returned else False
        """
        if not self.isDone():
            return False

        if self.imageCount() > 1:
            wikipedia.output("\"%s\" contains multiple images, ignoring" % self.page.title(),toStdout=True)
            return False

        if self.isWithdrawn():
            wikipedia.output("\"%s\" withdrawn, currently ignoring" % self.page.title(),toStdout=True)
            return False

        if self.isFPX():
            wikipedia.output("\"%s\" contains FPX, currently ignoring" % self.page.title(),toStdout=True)
            return False

        self.countVotes()

        result = "\n\n{{FPC-results-ready-for-review|support=%d|oppose=%d|neutral=%d|featured=%s|sig=~~~~}}" % \
            (self._support,self._oppose,self._neutral,"yes" if self.isFeatured() else "no")
            
        old_text = self.page.get()
        new_text = old_text + result
        
        self.commit(old_text,new_text,self.page)
        
        return True

        
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
            wikipedia.output("Could not retrieve history for '%s', returning now()" % self.page.title(),toStdout=True)
            return datetime.datetime.now()

        m = re.match(DateR,history[0][1].lower())
        self._creationTime = datetime.datetime(int(m.group(5)),
                                               Month[m.group(4)],
                                               int(m.group(3)),
                                               int(m.group(1)),
                                               int(m.group(2)))
        return self._creationTime
        

    def statusString(self):
        """
        A nomination can have three statuses:
         * Featured
         * Not featured
         * Active  ( not old enough )
        """
        if self.isIgnored():
            return "Ignored"
        elif self.isWithdrawn():
            return "Withdrawn"
        elif not self.isDone():
            return "Active"
        else:
            return "Featured" if self.isFeatured() else "Not featured"

    def daysOld(self):
        """Find the number of days this nomination has existed"""

        if self._daysOld != -1:
            return self._daysOld

        delta = datetime.datetime.now() - self.creationTime()
        self._daysOld = delta.days
        return self._daysOld

    def isDone(self):
        """
        Checks if a nomination can be closed
        """
        return self.daysOld() >= 9

    def isFeatured(self):
        """
        Find if an image can be featured.
        Does not check the age, it needs to be
        checked using isDone()
        """
        
        if self.isWithdrawn():
            return False

        if not self._votesCounted:
            self.countVotes()

        return self._support >= 5 and \
            (self._support >= 2*self._oppose)
    

    def isIgnored(self):
        """Some nominations currently require manual check"""
        return self.imageCount() > 1

    def sectionCount(self):
        """Count the number of sections in this candidate"""
        text = self.page.get()
        return len(re.findall(SectionR,text))

    def imageCount(self):
        """Count the number of images that are displayed"""
        text = self.page.get()
        return len(re.findall(ImagesR,text))

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
        text = self.page.get()
        return re.findall(PreviousResultR,text)

    def verifiedResult(self):
        xxx

    def compareResultToCount(self):
        """
        If there is an existing result we will compare
        it to a new vote count made by this bot and 
        see if they match. This is for testing purposes
        of the bot and to find any incorrect old results.
        """
        text = self.page.get()
        res = self.existingResult()

        if self.isWithdrawn():
            wikipedia.output("%s: (ignoring, was withdrawn)" % self.cutTitle(),toStdout=True)
            return

        elif self.isFPX():
            wikipedia.output("%s: (ignoring, was FPXed)" % self.cutTitle(),toStdout=True)
            return

        elif not res:
            wikipedia.output("%s: (ignoring, has no results)" % self.cutTitle(),toStdout=True)
            return

        elif len(res) > 1:
            wikipedia.output("%s: (ignoring, has several results)" % self.cutTitle(),toStdout=True)
            return

        # We have one result, so make a vote count and compare
        old_res = res[0]
        was_featured = (old_res[3] == u'featured')
        ws = int(old_res[0])
        wo = int(old_res[1])
        wn = int(old_res[2])
        self.countVotes()

        if self._support == ws and self._oppose == wo and self._neutral == wn and was_featured == self.isFeatured():
            status = "OK"
        else:
            status = "FAIL"

        # List info to console
        wikipedia.output("%s: S%02d/%02d O:%02d/%02d N%02d/%02d F%d/%d (%s)" % (self.cutTitle(),
                                                                                self._support,ws,
                                                                                self._oppose ,wo,
                                                                                self._neutral,wn,
                                                                                self.isFeatured(),was_featured,
                                                                                status),toStdout=True)

    def cutTitle(self):
        """Returns a fixed with title"""
        return re.sub(PrefixR,'',self.page.title())[0:50].ljust(50)

    def cleanTitle(self):
        """Returns a title string without prefix and extension"""
        noprefix =  re.sub(PrefixR,'',self.page.title())
        return re.sub(r'\.\w{1,3}$\s*','',noprefix)

    def fileName(self):
        """Return only the filename of this candidate"""
        # The regexp here also removes any possible crap between the prefix
        # and the actual start of the filename.
        return re.sub("(%s.*?)([File|Image])" % candPrefix,r'\2',self.page.title())

    def addToFeaturedList(self,category):
        """
        Will add this page to the list of featured images.
        This uses just the base of the category, like 'Animals'.
        Should only be called on closed and verified candidates
        
        This is ==STEP 1== of the parking procedure

        @param category The categorization category
        """
        # This function first needs to find the main category
        # then inside the gallery tags remove the last line and
        # add this candidate to the top

        listpage = 'Commons:Featured pictures, list'
        page = wikipedia.Page(wikipedia.getSite(), listpage)
        old_text = page.get()
        
        # Thanks KODOS for a nice regexp gui
        # This adds ourself first in the list of length 4 and removes the last
        # all in the chosen category
        ListPageR = re.compile(r"(^==\s*{{{\s*\d+\s*\|%s\s*}}}\s*==\s*<gallery.*>\s*)(.*\s*)(.*\s*.*\s*)(.*\s*)(</gallery>)" % category, re.MULTILINE)
        new_text = re.sub(ListPageR,r"\1%s\n\2\3\5" % self.fileName(), old_text)
        self.commit(old_text,new_text,page)

    def addToCategorizedFeaturedList(self,category):
        """
        Adds the candidate to the page with categorized featured
        pictures. This is the full category.

        This is ==STEP 2== of the parking procedure

        @param category The categorization category
        """
        catpage = "Commons:Featured pictures/" + category
        page = wikipedia.Page(wikipedia.getSite(), catpage)
        old_text = page.get()
        
        # We just need to append to the bottom of the gallery
        # with an added title
        new_text = re.sub('</gallery>',"%s\n</gallery>" % self.fileName() , old_text)
        self.commit(old_text,new_text,page);

    def addAssessments(self):
        """
        Adds the the assessments template to a featured
        pictures descripion page.

        This is ==STEP 3== of the parking procedure

        """
        asspage = self.fileName()
        page = wikipedia.Page(wikipedia.getSite(), asspage)
        old_text = page.get()
        
        AssR = re.compile(r'{{\s*[Aa]ssessments\s*\|(.*)}}')

        # First check if there already is an assessments template on the page
        params = re.search(AssR,old_text)
        if params:
            # Make sure to remove any existing com param
            params = re.sub(r"com\s*=\s*\d+\|?",'',params.group(1))
            params += "|com=1"
            new_ass = "{{Assessments|%s}}" % params
            new_text = re.sub(AssR,new_ass,old_text)
        else:
            # There is no assessments template so just add it
            end = findEndOfTemplate(old_text,"[Ii]nformation")
            new_text = old_text[:end] + "\n{{Assessments|com=1}}\n" + old_text[end:]
            #new_text = re.sub(r'({{\s*[Ii]nformation)',r'{{Assessments|com=1}}\n\1',old_text)

        self.commit(old_text,new_text,page)

    def addToCurrentMonth(self):
        """
        Adds the candidate to the list of featured picture this month

        This is ==STEP 4== of the parking procedure
        """
        monthpage = 'Commons:Featured_pictures/chronological/current_month'
        page = wikipedia.Page(wikipedia.getSite(), monthpage)
        old_text = page.get()

        #Find the number of lines in the gallery
        m = re.search(r"(?ms)<gallery>(.*)</gallery>",old_text)
        count = m.group(0).count("\n")

        # We just need to append to the bottom of the gallery
        # with an added title
        # TODO: We lack a good way to find the creator, so it is left out at the moment
        new_text = re.sub('</gallery>',"%s|%d '''%s''' <br> uploaded by %s, nominated by %s\n</gallery>" % 
                          (self.fileName(), count, self.cleanTitle(), self.uploader(), self.nominator()) , old_text)
        self.commit(old_text,new_text,page);
        
    def notifyNominator(self):
        """
        Add a template to the nominators talk page

        This is ==STEP 5== of the parking procedure
        """
        talk_link = "User_talk:%s" % self.nominator(link=False)
        talk_page = wikipedia.Page(wikipedia.getSite(), talk_link)
        old_text = talk_page.get()
        new_text = old_text + "\n\n== FP Promotion ==\n{{FPpromotion|%s}} /~~~~" % self.fileName()
        self.commit(old_text,new_text,talk_page)

    def moveToLog(self):
        """
        Remove this candidate from the current list 
        and add it to the log of the current month

        This is ==STEP 6== of the parking procedure
        """
        # Remove from current list
        candidate_page = wikipedia.Page(wikipedia.getSite(), "Commons:Featured picture candidates/candidate list")
        old_cand_text = candidate_page.get()
        new_cand_text = re.sub(r"{{\s*%s\s*}}.*?\n" % self.page.title(),'', old_cand_text)
        self.commit(old_cand_text,new_cand_text,candidate_page)
        
        # Add to log
        # (Note FIXME, we must probably create this page if it does not exist)
        today = datetime.date.today()
        current_month = Month2[today.month]
        log_link = "Commons:Featured picture candidates/Log/%s %s" % (current_month,today.year)
        log_page = wikipedia.Page(wikipedia.getSite(), log_link)
        old_log_text = log_page.get()
        new_log_text = old_log_text + "\n{{%s}}" % self.page.title()
        self.commit(old_log_text,new_log_text,log_page)

    def park(self):
        """This will do everything that is needed to park a closed candidate"""

        # First look for verified results
        text = self.page.get()
        results = re.findall(VerifiedResultR,text)
        
        if self.imageCount() > 1:
            wikipedia.output("%s: (ignoring, is multiimage)" % self.cutTitle(),toStdout=True)
            return

        if not results:
            wikipedia.output("%s: (ignoring, no verified results)" % self.cutTitle(),toStdout=True)
            return

        if len(results) > 1:
            wikipedia.output("%s: (ignoring, several verified results ?)" % self.cutTitle(),toStdout=True)
            return
        
        if self.isWithdrawn():
            wikipedia.output("%s: (ignoring, was withdrawn)" % self.cutTitle(),toStdout=True)
            return

        if self.isFPX():
            wikipedia.output("%s: (ignoring, was FPXed)" % self.cutTitle(),toStdout=True)
            return

        # Ok we should now have a candidate with verified results that we can park
        vres = results[0]
        if vres[3] == "yes":
            # Featured picture
            self.addToFeaturedList(re.sub(r'(.*?)/.*',r'\1',vres[4]))
            self.addToCategorizedFeaturedList(vres[4])
            self.addAssessments()
            self.addToCurrentMonth()
            self.notifyNominator()
            self.moveToLog()
        elif  vres[3] == "no":
            # Non Featured picure
            self.moveToLog()
        else:
            wikipedia.output("%s: (ignoring, unknown verified feature status '%s')" % (self.cutTitle(),vres[3]),toStdout=True)
            return


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
                wikipedia.output(u"\03{lightgreen}%s\03{default}" % line,newline=False,toStdout=True)
            elif line.startswith('- '):
                wikipedia.output(u"\03{lightred}%s\03{default}" % line,newline=False,toStdout=True)
            elif line.startswith('! '):
                wikipedia.output(u"\03{lightyellow}%s\03{default}" % line,newline=False,toStdout=True)
            else:
                wikipedia.output(line,newline=False,toStdout=True)
        wikipedia.output("\n",toStdout=True)

        choice = wikipedia.inputChoice(
            u"Do you want to accept these changes to '%s' ?" % page.title(),
            ['Yes', 'No', "Quit"],
            ['y', 'N', 'q'], 'N')
        
        #choice = 'n'

        if choice == 'y':
            wikipedia.output("Would have commited, but not implemented",toStdout=True)
            #page.put(new_text, comment=comment, watchArticle=True, minorEdit=False, maxTries=10 );
        elif choice == 'q':
            wikipedia.output("Aborting.",toStdout=True)
            sys.exit(0)
        else:
            wikipedia.output("Changes ignored",toStdout=True)
        

def findCandidates(page_url):
    """This finds all candidates on the main FPC page"""

    page = wikipedia.Page(wikipedia.getSite(), page_url)

    candidates = []
    templates = page.getTemplates()
    for template in templates:
        title = template.title()
        if title.startswith(candPrefix):
            #wikipedia.output("Adding '%s'" % title, toStdout = True)
            candidates.append(Candidate(template))
        else:
            pass
            #wikipedia.output("Skipping '%s'" % title, toStdout = True)
    return candidates

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
        if not lvl and ne < ns:
            return ne+2
        elif ne < ns:
            lvl -= 1
            cp = ne+2 
        else:
            lvl += 1
            cp = ns+2
    # Apparently we never found it
    return 0
        
    

# Exact description about what needs to be done with a closed nomination
#
# 1. Check whether the count is verified or not
# 2. If verified and featured:
#    * Add page to 'Commons:Featured pictures, list'
#    * Add to subpage of 'Commons:Featured pictures, list'
#    * Add {{Assessments|com=1}} or just the parameter if the template is already there 
#        to the picture page (should also handle subpages)
#    * Add the picture to the 'Commons:Featured_pictures/chronological/current_month'
#    * Add the template {{FPpromotion|File:XXXXX.jpg}} to the Talk Page of the nominator.
# 3. If featured or not move it from 'Commons:Featured picture candidates/candidate list'
#    to the log, f.ex. 'Commons:Featured picture candidates/Log/August 2009'

# Data and regexps used by the bot
Month  = { 'january':1, 'february':2, 'march':3, 'april':4, 'may':5, 'june':6, 'july':7, 'august':8, 'september':9, 'october':10, 'november':11, 'december':12 }
Month2  = { 1:'January', 2:'February', 3:'March', 4:'April', 5:'May', 6:'June', 7:'July', 8:'August', 9:'September', 10:'October', 11:'November', 12:'December' }
DateR = re.compile('(\d\d):(\d\d), (\d{1,2}) ([a-z]+) (\d{4})')

# List of valid templates
# They are taken from the page Commons:Polling_templates and some common redirects
support_templates = (u'[Ss]upport',u'[Pp]ro',u'[Ss]im',u'[Tt]ak',u'[Ss]í',u'[Pp]RO',u'[Ss]up',u'[Yy]es',u'[Oo]ui',u'[Kk]yllä', # First support + redirects
                     u'падтрымліваю',u'[Aa] favour',u'[Pp]our',u'[Tt]acaíocht',u'[Cc]oncordo',u'בעד', 
                     u'[Ss]amþykkt',u'支持',u'찬성',u'[Ss]for',u'за',u'[Ss]tödjer',u'เห็นด้วย',u'[Dd]estek')
oppose_templates  = (u'[Oo]ppose',u'[Kk]ontra',u'[Nn]ão',u'[Nn]ie',u'[Mm]autohe',u'[Oo]pp',u'[Nn]ein',u'[Ee]i', # First oppose + redirect
                     u'[Cс]упраць',u'[Ee]n contra',u'[Cc]ontre',u'[Ii] gcoinne',u'[Dd]íliostaigh',u'[Dd]iscordo',u'נגד',u'á móti',u'反対',u'除外',u'반대',
                     u'[Mm]ot',u'против',u'[Ss]tödjer ej',u'ไม่เห็นด้วย',u'[Kk]arsi',u'FPX contested')
neutral_templates = (u'[Nn]eutral?',u'[Oo]partisk',u'[Nn]eutre',u'[Nn]eutro',u'נמנע',u'[Nn]øytral',u'中立',u'Нэўтральна',u'[Tt]arafsız',u'Воздерживаюсь',
                     u'[Hh]lutlaus',u'중립',u'[Nn]eodrach',u'เป็นกลาง','[Vv]n')

# 
# Compiled regular expressions follows
#

# Used to remove the prefix and just print the file names
# of the candidate titles.
PrefixR = re.compile("%s.*?([Ff]ile|[Ii]mage)?:" % candPrefix)

# Looks for result counts, an example of such a line is:
# '''result:''' 3 support, 2 oppose, 0 neutral => not featured.
#
PreviousResultR = re.compile('\'\'\'result:\'\'\'\s+(\d+)\s+support,\s+(\d+)\s+oppose,\s+(\d+)\s+neutral\s*=>\s*((?:not )?featured)',re.MULTILINE)

# Looks for verified results
VerifiedResultR = re.compile(r'{{\s*FPC-results-reviewed\s*\|\s*support\s*=\s*(\d+)\s*\|\s*oppose\s*=\s*(\d+)\s*\|\s*neutral\s*=\s*(\d+)\s*\|\s*featured\s*=\s*(\w+)\s*\|\s*category\s*=\s*([^|]*).*}}',re.MULTILINE)

# Is whitespace allowed at the end ?
SectionR = re.compile('^={1,4}.+={1,4}\s*$',re.MULTILINE)
# Voting templates
SupportR = re.compile("{{\s*(?:%s)(\|.*)?\s*}}" % "|".join(support_templates),re.MULTILINE)
OpposeR  = re.compile("{{\s*(?:%s)(\|.*)?\s*}}" % "|".join( oppose_templates),re.MULTILINE)
NeutralR = re.compile("{{\s*(?:%s)(\|.*)?\s*}}" % "|".join(neutral_templates),re.MULTILINE)
# Striked out votes 
StrikedOutSupportR = re.compile("<s>.*{{\s*(?:%s)(\|.*)?\s*}}.*</s>" % "|".join(support_templates),re.MULTILINE)
StrikedOutOpposeR  = re.compile('<s>.*{{\s*(?:%s)(\|.*)?\s*}}.*</s>' % "|".join( oppose_templates),re.MULTILINE)
StrikedOutNeutralR = re.compile('<s>.*{{\s*(?:%s)(\|.*)?\s*}}.*</s>' % "|".join(neutral_templates),re.MULTILINE)
# Finds if a withdraw template is used
# This template has an optional string which we
# must be able to detect after the pipe symbol
WithdrawnR = re.compile('{{\s*[wW]ithdraw\s*(\|.*)?}}',re.MULTILINE)
# Nomination that contain the fpx template
FpxR = re.compile('{{\s*FPX(\|.*)?}}',re.MULTILINE)
# Counts the number of displayed images
ImagesR = re.compile('\[\[(File|Image):.+\]\]',re.MULTILINE)

def main(*args):

    fpcTitle = 'Commons:Featured picture candidates/candidate list'
    testLog = 'Commons:Featured_picture_candidates/Log/January_2009'

    worked = False

    for arg in wikipedia.handleArgs(*args):
        worked = True
        if arg == '-test':
            for candidate in findCandidates(testLog):
                try:
                    candidate.compareResultToCount()
                except wikipedia.IsRedirectPage:
                    pass
        elif arg == '-close':
            for candidate in findCandidates(fpcTitle):
                candidate.closePage()
        elif arg == '-info':
            for candidate in findCandidates(fpcTitle):
                try:
                    candidate.printAllInfo()
                except wikipedia.NoPage:
                    wikipedia.output("No such page '%s'" % candidate.page.title(), toStdout = True)
                    pass
        elif arg == '-park':
            for candidate in findCandidates(fpcTitle):
                try:
                    candidate.park()
                except wikipedia.NoPage:
                    wikipedia.output("No such page '%s'" % candidate.page.title(), toStdout = True)
                    pass
                    
        else:
            wikipedia.output("Warning - unknown argument '%s', see -help." % arg, toStdout = True)

    if not worked:
        wikipedia.output("Warning - you need to specify an argument, see -help.", toStdout = True)
            

if __name__ == "__main__":
    try:
        main()
    finally:
        wikipedia.stopme()

