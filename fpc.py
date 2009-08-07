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

    def countVotes(self):
        """
        Counts all the votes for this nomnination
        """

        if self._votesCounted:
            return

        # TODO: templatesWithParams() was _much_ slower
        #       than using getTemplates(), could be optimized.
        templates = self.page.templatesWithParams()
        for template in templates:
            title = template[0]
            #wikipedia.output(title, toStdout = True)
            if title == "Oppose":
                self._oppose += 1
            elif title == "Support":
                self._support += 1
            elif title == "Neutral":
                self._neutral += 1
            else:
                self._unknown += 1

        self._votesCounted = True

        wikipedia.output("%s: S:%02d O:%02d N:%02d U:%02d D:%02d Se:%d (%s)" % 
                         ( self.page.title().replace(candPrefix,'')[0:40].ljust(40),
                           self._support,self._oppose,self._neutral,self._unknown,
                           self.daysOld(),self.sectionCount(),self.statusString()),
                         toStdout = True)

    def closePage(self):
        """
        Will add the voting results to the page if it is finished.
        If it was, True is returned else False
        """
        if not self.isDone():
            return False

        if self.sectionCount() > 1:
            wikipedia.output("%s contains multiple sections, ignoring" % self.page.title(),toStdout=True)
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
        if not self.isDone():
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
        
        if not self._votesCounted:
            self.countVotes()

        return self._support >= 5 and \
            (self._support >= 2*self._oppose)
    

    def sectionCount(self):
        """Count the number of sections in this candidate"""
        text = self.page.get()
        return len(re.findall(SectionR,text))

def findCandidates(page):
    """This finds all candidates on the main FPC page"""
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
# Is whitespace allowed at the end ?
SectionR = re.compile('^={1,4}.+={1,4}\s*$',re.MULTILINE)

def main():

    fpcTitle = 'Commons:Featured picture candidates/candidate list';
    fpcPage = wikipedia.Page(wikipedia.getSite(), fpcTitle)

    for candidate in findCandidates(fpcPage):
        candidate.closePage()

if __name__ == "__main__":
    try:
        main()
    finally:
        wikipedia.stopme()

