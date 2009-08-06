#
# Testing FPC
#

# TODO: catch exceptions

import wikipedia

candPrefix = "Commons:Featured picture candidates/"

def main():

    fpcTitle = 'Commons:Featured picture candidates/candidate list';
    fpcPage = wikipedia.Page(wikipedia.getSite(), fpcTitle)

    for candidate in findCandidates(fpcPage):
        countVotes(candidate)
            

def findCandidates(page):
    candidates = []
    templates = page.getTemplates()
    for template in templates:
        title = template.title()
        if title.startswith(candPrefix):
            candidates.append(template)
        else:
            pass
            #wikipedia.output("Skipping '%s'" % title, toStdout = True)
    return candidates


def countVotes(candidate):
    #wikipedia.output(candidate.title(), toStdout = True)

    oppose  = 0
    support = 0
    neutral = 0
    unknown = 0

    # TODO: templatesWithParams() was _much_ slower
    #       than using getTemplates(), could be optimized.
    templates = candidate.templatesWithParams()
    for template in templates:
        title = template[0]
        #wikipedia.output(title, toStdout = True)
        if title == "Oppose":
            oppose += 1
        elif title == "Support":
            support += 1
        elif title == "Neutral":
            neutral += 1
        else:
            unknown += 1

    wikipedia.output("%s: S:%02d O:%02d N:%02d U:%02d (%s)" % 
                     ( candidate.title().replace(candPrefix,'')[0:40].ljust(40),
                       support,oppose,neutral,unknown,
                       "Featured" if isFeatured(support,oppose) else "Not featured"), 
                     toStdout = True)

def isFeatured(support,oppose):
    return (support >= 2*oppose) and support >= 5

if __name__ == "__main__":
    try:
        main()
    finally:
        wikipedia.stopme()

