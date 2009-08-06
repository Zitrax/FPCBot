#
# Testing FPC
#

# TODO: catch exceptions

import wikipedia

candPrefix = "Commons:Featured picture candidates/"

class Candidate():
    """This is one feature picture candidate"""

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

        wikipedia.output("%s: S:%02d O:%02d N:%02d U:%02d (%s)" % 
                         ( self.page.title().replace(candPrefix,'')[0:40].ljust(40),
                           self._support,self._oppose,self._neutral,self._unknown,
                           "Featured" if self.isFeatured() else "Not featured"), 
                         toStdout = True)

    def isFeatured(self):
        return (self._support >= 2*self._oppose) and self._support >= 5
    
    

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

