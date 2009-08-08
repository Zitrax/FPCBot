#
# Testing FPC
#

# TODO: catch exceptions

import wikipedia, re, datetime

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
        self.page = page
        self._oppose  = 0
        self._support = 0
        self._neutral = 0
        self._unknown = 0
        self._votesCounted = False
        self._featured = False
        self._daysOld = -1
        self._creationTime = None
        self._striked = None

    def printAllInfo(self):
        """
        Console output of all information sought after
        """
        self.countVotes()
        wikipedia.output("%s: S:%02d(-%02d) O:%02d(-%02d) N:%02d U:%02d D:%02d Se:%d Im:%02d W:%s (%s)" % 
                         ( self.page.title().replace(candPrefix,'')[0:40].ljust(40),
                           self._support,self._striked[0],self._oppose,self._striked[1],
                           self._neutral,self._unknown,
                           self.daysOld(),self.sectionCount(),
                           self.imageCount(),self.isWithdrawn(),
                           self.statusString()),
                         toStdout = True)


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
        self._striked = (s_support,s_oppose)
        return self._striked
        

    def isWithdrawn(self):
        """
        Withdrawn nominations should not be counted
        """
        return len(re.findall(WithdrawnR,self.page.get()))

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

        self.countVotes()

        result = "\n\n '''result:''' %d support, %d oppose, %d neutral => %s. /~~~~" % \
            (self._support,self._oppose,self._neutral,self.statusString())
            
        old_text = self.page.get()
        new_text = old_text + result
        
        # Show the diff
        wikipedia.output(u"\n\n>>> \03{lightpurple}%s\03{default} <<<"
                         % self.page.title())
        wikipedia.showDiff(old_text, new_text)

        return True

        
    def creationTime(self):
        """Find the time that this candidate were created"""
        if self._creationTime:
            return self._creationTime

        history = self.page.getVersionHistory(reverseOrder=True,revCount=1)
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
        The reuturn value is a list of tuples, and normally
        there should only be one such tuple. The tuple
        contains four values:
        support,oppose,neutral,(featured|not featured)
        """
        text = self.page.get()
        return re.findall(PreviousResultR,text)

    def compareResultToCount(self):
        """
        If there is an existing result we will compare
        it to a new vote count made by this bot and 
        see if they match. This is for testing purposes
        of the bot and to find any incorrect old results.
        """
        text = self.page.get()
        res = self.existingResult()

        if not res:
            wikipedia.output("%s (ignoring, has no results)" % self.page.title(),toStdout=True)
            return

        if len(res) > 1:
            wikipedia.output("%s (ignoring, has several results)" % self.page.title(),toStdout=True)
            return

        # We have one result, so make a vote count and compare
        old_res = res[0]
        was_featured = (old_res[3] == u'featured')
        ws = int(old_res[0])
        wo = int(old_res[1])
        wn = int(old_res[2])
        self.countVotes()

        if self._support == ws and self._oppose == wo and self._neutral == wn and was_featured == self.isFeatured():
            status = "Matching results, OK"
        else:
            status = "Inconsistant results, FAIL"

        # List info to console
        wikipedia.output("%s: S%02d/%02d O:%02d/%02d N%02d:%02d F%d/%d (%s)" % (self.page.title().replace(candPrefix,'')[0:40].ljust(40),
                                                                                self._support,ws,
                                                                                self._oppose ,wo,
                                                                                self._neutral,wn,
                                                                                self.isFeatured(),was_featured,
                                                                                status),toStdout=True)


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


# Data and regexps used by the bot
Month  = { 'january':1, 'february':2, 'march':3, 'april':4, 'may':5, 'june':6, 'july':7, 'august':8, 'september':9, 'october':10, 'november':11, 'december':12 }
DateR = re.compile('(\d\d):(\d\d), (\d{1,2}) ([a-z]+) (\d{4})')

# List of valid templates
support_templates = ('[Ss]upport','[Ss]up','[Pp]ro')
oppose_templates  = ('[Oo]ppose','[Oo]pp')  
neutral_templates = ('[Nn]eutral')

# 
# Compiled regular expressions follows
#

# Looks for result counts, an example of such a line is:
# '''result:''' 3 support, 2 oppose, 0 neutral => not featured.
#
PreviousResultR = re.compile('\'\'\'result:\'\'\'\s+(\d)+\s+support,\s+(\d)+\s+oppose,\s+(\d)+\s+neutral\s+=>\s+((?:not )?featured)',re.MULTILINE)

# Is whitespace allowed at the end ?
SectionR = re.compile('^={1,4}.+={1,4}\s*$',re.MULTILINE)
# Voting templates
SupportR = re.compile("{{\s*(?:%s)\s*}}" % "|".join(support_templates),re.MULTILINE)
OpposeR  = re.compile("{{\s*(?:%s)\s*}}" % "|".join( oppose_templates),re.MULTILINE)
NeutralR = re.compile("{{\s*(?:%s)\s*}}" % "|".join(neutral_templates),re.MULTILINE)
# Striked out votes 
StrikedOutSupportR = re.compile('<s>.*{{\s*[sS]up(port)?\s*}}.*</s>',re.MULTILINE)
StrikedOutOpposeR  = re.compile('<s>.*{{\s*[oO]pp(ose)?\s*}}.*</s>' ,re.MULTILINE)
# Finds if a withdraw template is used
# This template has an optional string which we
# must be able to detect after the pipe symbol
WithdrawnR = re.compile('{{\s*[wW]ithdraw\s*(\|.*)?}}',re.MULTILINE)
# Counts the number of displayed images
ImagesR = re.compile('\[\[(File|Image):.+\]\]',re.MULTILINE)

def main():

    print "{{\s*(?:%s)\s*}}" % "|".join(neutral_templates)

    fpcTitle = 'Commons:Featured picture candidates/candidate list'
    testLog = 'Commons:Featured_picture_candidates/Log/January_2009'

    for candidate in findCandidates(testLog):
        #candidate.closePage()
        #candidate.printAllInfo()
        candidate.compareResultToCount()

if __name__ == "__main__":
    try:
        main()
    finally:
        wikipedia.stopme()

