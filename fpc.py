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

    def countVotes(self):
        #wikipedia.output(candidate.title(), toStdout = True)


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

        wikipedia.output("%s: S:%02d O:%02d N:%02d U:%02d D:%02d (%s)" % 
                         ( self.page.title().replace(candPrefix,'')[0:40].ljust(40),
                           self._support,self._oppose,self._neutral,self._unknown,
                           self.daysOld(),
                           "Featured" if self.isFeatured() else "Not featured"), 
                         toStdout = True)

    def creationTime(self):
        """Find the time that this candidate were created"""
        history = self.page.getVersionHistory(reverseOrder=True,revCount=1)
        
        # Could be compiled
        m = re.search('(\d\d):(\d\d), (\d{1,2}) ([a-z]+) (\d{4})',history[0][1].lower())
        return  datetime.datetime(int(m.group(5)),
                                  Month[m.group(4)],
                                  int(m.group(3)),
                                  int(m.group(1)),
                                  int(m.group(2)))
    
    def daysOld(self):
        """Find the number of days this nomination has existed"""
        old = datetime.datetime.now() - self.creationTime()
        return old.days

    def isFeatured(self):
        """
        Find if an image can be featured or not.

        An image can be featured if all of this is true:
        * There are 5 or more support votes
        * The ratio of support/oppose >= 2/1
        * 9 days has passed since nomination
        """
        
        return (self._support >= 2*self._oppose) and self._support >= 5 and self.daysOld() >= 9
    

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


Month  = { 'january':1, 'february':2, 'march':3, 'april':4, 'may':5, 'june':6, 'july':7, 'august':8, 'september':9, 'october':10, 'november':11, 'december':12 }

def main():

    fpcTitle = 'Commons:Featured picture candidates/candidate list';
    fpcPage = wikipedia.Page(wikipedia.getSite(), fpcTitle)

    for candidate in findCandidates(fpcPage):
        candidate.countVotes()

if __name__ == "__main__":
    try:
        main()
    finally:
        wikipedia.stopme()

