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
from __future__ import annotations
import sys
import abc
from collections.abc import Callable
from types import FrameType
from typing import Final, Literal, ClassVar
import signal
import datetime
import time
import re
import urllib.parse
import threading
import traceback

# Third-party imports
import pywikibot
from pywikibot import config


# CONSTANTS

# Namespaces, prefixes, page names, etc.
BOT_NAME: Final[str] = "FPCBot"
FILE_NAMESPACE: Final[str] = "File:"
USER_NAMESPACE: Final[str] = "User:"
USER_TALK_NAMESPACE: Final[str] = "User talk:"
FP_PREFIX: Final[str] = "Commons:Featured pictures/"
FPC_PAGE: Final[str] = "Commons:Featured picture candidates"
CAND_PREFIX: Final[str] = f"{FPC_PAGE}/"
CAND_LOG_PREFIX: Final[str] = f"{CAND_PREFIX}Log/"
CHRONO_ARCHIVE_PREFIX: Final[str] = f"{FP_PREFIX}chronological/"
CAND_LIST_PAGE_NAME: Final[str] = f"{CAND_PREFIX}candidate list"
TEST_LOG_PAGE_NAME: Final[str] = f"{CAND_LOG_PREFIX}January 2025"
GALLERY_LIST_PAGE_NAME: Final[str] = "Commons:Featured pictures, list"
FP_TALK_PAGE_NAME: Final[str] = "Commons talk:Featured picture candidates"
UNSORTED_HEADING: Final[str] = "Unsorted"


# Valid voting templates
# Taken from Commons:Polling_templates, including some common redirects
SUPPORT_TEMPLATES: Final[tuple[str, ...]] = (
    "[Ss]upport",
    "[Ss]upp?",
    "[Ss]",
    "[Vv]ote[ _]support",
    "[Ss]trong[ _]support",
    "[Ss]Support",
    "[Ww]eak[ _]support",
    "[Ww]eak[ _][Ss]",
    "[Ww]Support",
    # Variants and translations
    "[Aa][ _]favore?",
    "[Aa][ _]favuri",
    "[Aa]poio",
    "[AaÁá][ _]fabor",
    "[Dd]estek",
    "[Kk]yllä",
    "[Oo]ui",
    "[Pp]our",
    "[Pp]ro",
    "[Pp]RO",
    "[Ss][Ff]or",
    "[Ss]amþykkt",
    "[Ss]im",
    "[Ss]tödjer",
    "[Ss]ubteno",
    "[Ss]up",
    "[Ss]í",
    "[Tt]acaíocht",
    "[Tt]ak",
    "[Tt]aurä",
    "[Vv]oor",
    "[Yy]es",
    "[Υυ]πέρ",
    "[Зз]а",
    "[Пп]адтрымліваю",
    "דעב",
    "เห็นด้วย",
    "ჰო",
    "支持",
    "賛成",
    "찬성",
)
OPPOSE_TEMPLATES: Final[tuple[str, ...]] = (
    "[Oo]ppose",
    "[Oo]pp",
    "[Oo]",
    "[Ss]trong[ _]oppose",
    "[Ss]Oppose",
    "[Ww]eak[ _]oppose",
    "[Ww]eak[ _][Oo]",
    "[Ww]eako",
    # {{FPX contested}} is counted like a normal {{oppose}} vote
    "FPX[ _]contested",
    # Variants and translations
    "[Cc]ontr[aeo]",
    "[Cc]ontrario",
    "[Cc]untrariu",
    "[Dd]iscordo",
    "[Dd]íliostaigh",
    "[Ee]i",
    "[Ee]n[ _]contra",
    "[Ii][ _]gcoinne",
    "[Kk]ar[sş]i",
    "[Kk]ontra",
    "[Kk]ontraŭi",
    "[Ll]ivari",
    "[Mm]autohe",
    "[Mm]ot",
    "[Nn]ein",
    "[Nn]ie",
    "[Nn]o",
    "[Nn]ão",
    "[Oo]pponera",
    "[Ss]tödjer[ _]ej",
    "[Tt]egen",
    "[Áá][ _]móti",
    "[Пп]ротив",
    "[Сс]упраць",
    "נגד",
    "ไม่เห็นด้วย",
    "反対",
    "除外",
    "반대",
)
NEUTRAL_TEMPLATES: Final[tuple[str, ...]] = (
    "[Nn]eutral",
    "[Nn]eu",
    "[Nn]",
    "[Vv]n",
    # Variants and translations
    "[Hh]lutlaus",
    "[Nn]eodrach",
    "[Nn]eutr[aeo]",
    "[Nn]eutrale",
    "[Nn]eŭtrala",
    "[Nn]iutrali",
    "[Nn]øytral",
    "[Oo]partisk",
    "[Tt]arafs[iı]z",
    "[Вв]оздерживаюсь",
    "[Вв]оздржан",
    "[Нн]эўтральна",
    "נמנע",
    "เป็นกลาง",
    "中立",
    "중립",
)
DELIST_TEMPLATES: Final[tuple[str, ...]] = (
    "[Dd]elist",
    # There seem to be no internationalized delist versions.
    # Don't add {{Remove}} or {{Del}}, they are for deletion discussions.
)
KEEP_TEMPLATES: Final[tuple[str, ...]] = (
    "[Kk]eep",
    "[Kk]",
    "[Vv]ote[ _]keep",
    "[Vv]keep",
    "[Vv]k",
    "[Ss]trong[ _]keep",
    "[Ww]eak[ _]keep",
    "[Ww]k",
    "[Vv]wk",
    # Variants and translations
    "[Bb]ehalten",
    "[Bb]ehold",
    "[Bb]ehåll",
    "[Cc]onserver",
    "[Gg]arder",
    "[Mm]antenere?",
    "[Mm]anter",
    "[Ss]avi",
    "[Зз]адржи",
    "เก็บ",
    "保留",
    "維持",
)


# Shared messages

NOMINATION_SUBPAGE_RENAMED: Final[str] = (
    "Fixed nomination subpage name because it could cause problems "
    "with [[Template:Assessments]] and other tools."
)
PLEASE_FIX_HINT: Final[str] = (
    "Please check and fix this so that the bot can process the nomination."
)
PLEASE_RENAME_HINT: Final[str] = (
    "Please rename [[{subpage}]] to a suitable name "
    "so that the bot can process the nomination."
)
SERIOUS_PROBLEM_CHECK_PAGE: Final[str] = (
    "This is a serious problem, please check that page."
)
PLEASE_CHECK_GALLERY_AND_SORT_FPS: Final[str] = (
    "Please check that gallery page and add the new featured picture(s) "
    "from the nomination to the appropriate gallery page."
)
LIST_INCLUDES_MISSING_SUBPAGE: Final[str] = (
    "The candidate list [[{list}]] includes the nomination [[{subpage}]], "
    "but that page does not exist. Perhaps the page has been renamed "
    f"and the list needs to be updated. {PLEASE_FIX_HINT}"
)
COULD_NOT_READ_RECENT_FPS_LIST: Final[str] = (
    f"The bot could not read [[{GALLERY_LIST_PAGE_NAME}|the list]] "
    "of recent Featured pictures: {exception}. "
    "Please check the list page and fix it."
)
ADDING_FPS_TO_UNSORTED_SECTION: Final[str] = (
    "Therefore one or more new featured pictures are added "
    "to the ''Unsorted'' section at the bottom of [[{page}]]. "
    "Please sort these images into the correct section."
)


# Compiled regular expressions

# Used to remove the nomination page prefix and the 'File:'/'Image:' namespace
# or to replace both by the standard 'File:' namespace.
# Removes also any possible crap between the prefix and the namespace
# and faulty spaces between namespace and filename (sometimes users
# accidentally add such spaces when creating nominations).
PREFIX_REGEX: Final[re.Pattern] = re.compile(
    CAND_PREFIX.replace(" ", r"[ _]") + r".*?([Ff]ile|[Ii]mage): *"
)

# Look for results using the old, text-based results format
# which was in use until August 2009.  An example of such a line is:
# '''result:''' 3 support, 2 oppose, 0 neutral => not featured.
OBSOLETE_RESULT_REGEX: Final[re.Pattern] = re.compile(
    r"'''[Rr]esult:'''\s+(\d+)\s+support,\s+(\d+)\s+oppose,\s+(\d+)\s+neutral"
    r"\s*=>\s*((?:not )?featured)"
)
OBSOLETE_DELIST_RESULT_REGEX: Final[re.Pattern] = re.compile(
    r"'''[Rr]esult:'''\s+(\d+)\s+delist,\s+(\d+)\s+keep,\s+(\d+)\s+neutral"
    r"\s*=>\s*((?:not )?delisted)"
)

# Look for verified results using the new, template-based format
VERIFIED_RESULT_REGEX: Final[re.Pattern] = re.compile(
    r"""
    \{\{\s*FPC-results-reviewed\s*\|
    \s*support\s*=\s*(\d+)\s*\|            # (1) Support votes
    \s*oppose\s*=\s*(\d+)\s*\|             # (2) Oppose votes
    \s*neutral\s*=\s*(\d+)\s*\|            # (3) Neutral votes
    \s*featured\s*=\s*(\w+)\s*\|           # (4) Featured, should be 'yes'/'no'
    \s*gallery\s*=\s*([^|\n]*)             # (5) Gallery link (if featured)
    (?:\|\s*alternative\s*=\s*([^|\n]*))?  # (6) For candidates with alternatives: name of the winning image
    .*?\}\}
    """,
    flags=re.VERBOSE,
)
VERIFIED_DELIST_RESULT_REGEX: Final[re.Pattern] = re.compile(
    r"""
    \{\{\s*FPC-delist-results-reviewed\s*\|
    \s*delist\s*=\s*(\d+)\s*\|   # (1) Delist votes
    \s*keep\s*=\s*(\d+)\s*\|     # (2) Keep votes
    \s*neutral\s*=\s*(\d+)\s*\|  # (3) Neutral votes
    \s*delisted\s*=\s*(\w+)      # (4) Delisted, should be 'yes'/'no'
    .*?\}\}
    """,
    flags=re.VERBOSE,
)

# Simple regexes which check just whether a certain template is present or not
COUNTED_TEMPLATE_REGEX: Final[re.Pattern] = re.compile(
    r"\{\{\s*FPC-results-unreviewed.*?\}\}"
)
DELIST_COUNTED_TEMPLATE_REGEX: Final[re.Pattern] = re.compile(
    r"\{\{\s*FPC-delist-results-unreviewed.*?\}\}"
)
REVIEWED_TEMPLATE_REGEX: Final[re.Pattern] = re.compile(
    r"\{\{\s*FPC-results-reviewed.*?\}\}"
)
DELIST_REVIEWED_TEMPLATE_REGEX: Final[re.Pattern] = re.compile(
    r"\{\{\s*FPC-delist-results-reviewed.*?\}\}"
)

# Find voting templates
VOTING_TEMPLATE_MODEL: Final[str] = r"\{\{\s*(?:%s)\s*(\|.*?)?\s*\}\}"
SUPPORT_VOTE_REGEX: Final[re.Pattern] = re.compile(
    VOTING_TEMPLATE_MODEL % "|".join(SUPPORT_TEMPLATES)
)
OPPOSE_VOTE_REGEX: Final[re.Pattern] = re.compile(
    VOTING_TEMPLATE_MODEL % "|".join(OPPOSE_TEMPLATES)
)
NEUTRAL_VOTE_REGEX: Final[re.Pattern] = re.compile(
    VOTING_TEMPLATE_MODEL % "|".join(NEUTRAL_TEMPLATES)
)
DELIST_VOTE_REGEX: Final[re.Pattern] = re.compile(
    VOTING_TEMPLATE_MODEL % "|".join(DELIST_TEMPLATES)
)
KEEP_VOTE_REGEX: Final[re.Pattern] = re.compile(
    VOTING_TEMPLATE_MODEL % "|".join(KEEP_TEMPLATES)
)

# Does the nomination contain a {{Withdraw(n)}}/{{Wdn}} template?
WITHDRAWN_REGEX: Final[re.Pattern] = re.compile(
    r"\{\{\s*[Ww](?:ithdrawn?|dn)\s*(\|.*?)?\}\}"
)
# Does the nomination contain a {{FPX}} or {{FPD}} template?
FPX_FPD_REGEX: Final[re.Pattern] = re.compile(
    r"\{\{\s*FP[XD]\s*(\|.*?)?\}\}"
)
# Does the nomination contain subheadings = subsections?
SECTION_REGEX: Final[re.Pattern] = re.compile(
    r"^={1,4}.+={1,4}\s*$", flags=re.MULTILINE
)
# Count the number of displayed images
IMAGES_REGEX: Final[re.Pattern] = re.compile(
    r"(\[\[((?:[Ff]ile|[Ii]mage):[^|]+).*?\]\])"
)
# Look for a size specification in the image link
IMAGE_SIZE_REGEX: Final[re.Pattern] = re.compile(r"\|.*?(\d+)\s*px")
# Check if there is a 'thumb' parameter in the image link
IMAGE_THUMB_REGEX: Final[re.Pattern] = re.compile(r"\|\s*thumb\b")
# Search nomination for the username of the original creator
CREATOR_NAME_REGEX: Final[re.Pattern] = re.compile(
    r"\{\{[Ii]nfo\}\}.+?"
    r"[Cc]reated +(?:(?:and|\&) +(?:[a-z]+ +)?uploaded +)?by +"
    r"\[\[[Uu]ser:([^|\]\n]+)[|\]]"
)
# Search for first entry in (or end of) <gallery>...</gallery> contents
GALLERY_ENTRY_START_REGEX: Final[re.Pattern] = re.compile(r">(?: *\n)*")

# Find the {{Assessments}} template on an image description page
# (sometimes people break it into several lines, so use '\s' and re.DOTALL)
ASSESSMENTS_TEMPLATE_REGEX: Final[re.Pattern] = re.compile(
    r"\{\{\s*[Aa]ssessments\s*(\|.*?)\}\}", flags=re.DOTALL
)

# Find the associated <gallery> element right after the target subheading.
# Between subheading and <gallery> we allow only:
# 1. a single line (e.g. a 'See also' hint) which must not be a subheading,
# <gallery>, etc.; or
# 2. a comment which can span several lines.
ASSOC_GALLERY_ELEMENT_REGEX: Final[re.Pattern] = re.compile(
    r"((?:[^<= \n][^\n]+\n\s*| *<!--.+?-->\s*)?<gallery\b[^>]*>)\s*",
    flags=re.DOTALL,
)

# Find 'Featured pictures of/from/by ...' categories which must be removed
# if a FP is delisted.  If the category is followed by a NL, remove it, too.
# NB: We must not touch project-specific categories like 'Featured pictures
# on Wikipedia, <language>', hence the negative lookahead assertion '(?!on )'.
TOPICAL_FP_CATEGORY_REGEX: Final[re.Pattern] = re.compile(
    r"\[\[[Cc]ategory: *Featured (?:"
    r"pictures (?!on ).+?"
    r"|(?:[a-z -]+)?photo(?:graphs|graphy|s).*?"
    r"|(?:diagrams|maps).*?"
    r")\]\] *\n?"
)


# GLOBALS

# Apply all changes without asking the user
_g_auto: bool = False
# Dry run, do not save any changes
_g_dry: bool = False
# Use threads
_g_threads: bool = False
# Don't print timestamps in CLI/log output
_g_log_no_time: bool = False
# Process only nominations with a name matched by this pattern
_g_match_pattern: str = ""
# Flag that will be set to True if CTRL-C was pressed
_g_abort: bool = False
# Pywikibot Site object
_g_site: pywikibot.site.BaseSite | None = None


# CLASSES

class ThreadCheckCandidate(threading.Thread):
    """
    A simple thread subclass representing and handling the execution
    of one of the bot's task on a certain candidate (nomination).
    """

    # Declare types of instance variables
    _candidate: Candidate  # The candidate/nomination handled in this thread
    _check: Callable[[Candidate], None]  # The method for the desired task

    def __init__(
        self,
        candidate: Candidate,
        check: Callable[[Candidate], None],
    ) -> None:
        """
        The initializer initializes the thread and saves references
        to the Candidate instance and to the method which should be called.
        """
        super().__init__()
        self._candidate = candidate
        self._check = check

    def run(self) -> None:
        """Execute the desired task for the candidate."""
        self._check(self._candidate)


class Candidate(abc.ABC):
    """
    Abstract base class for featured picture candidates/nominations,
    bundles all common properties and methods.
    The individual candidates/nominations are represented by instances
    of the concrete subclasses.
    """

    # Define class constants
    # (these are the values for a normal FP nomination,
    # subclasses must adapt them as needed)
    # Keyword for the title etc. of a successful nomination:
    _SUCCESS_KEYWORD: ClassVar[str] = "featured"
    # Keyword for the title etc. of a failed nomination:
    _FAIL_KEYWORD: ClassVar[str] = "not featured"
    # Compiled regex to find positive votes in the nomination:
    _PRO_VOTE_REGEX: ClassVar[re.Pattern] = SUPPORT_VOTE_REGEX
    # Compiled regex to find negative votes in the nomination:
    _CONTRA_VOTE_REGEX: ClassVar[re.Pattern] = OPPOSE_VOTE_REGEX
    # Compiled regex to find neutral votes in the nomination:
    _NEUTRAL_VOTE_REGEX: ClassVar[re.Pattern] = NEUTRAL_VOTE_REGEX
    # Compiled regex to find templates containing unreviewed results:
    _COUNTED_RES_REGEX: ClassVar[re.Pattern] = COUNTED_TEMPLATE_REGEX
    # Compiled regex to find templates containing reviewed results:
    _REVIEWED_RES_REGEX: ClassVar[re.Pattern] = REVIEWED_TEMPLATE_REGEX
    # Compiled regex to analyse the template with reviewed results:
    _VERIFIED_RES_REGEX: ClassVar[re.Pattern] = VERIFIED_RESULT_REGEX
    # Compiled regex to analyse the obsolete format for reviewed results:
    _OBSOLETE_RES_REGEX: ClassVar[re.Pattern] = OBSOLETE_RESULT_REGEX

    # Declare types of instance variables
    _list_page_name: str  # Name of the candidates list page on Commons
    _page: pywikibot.Page  # The nomination subpage for this candidate
    _filtered_content: str | None  # Text of the nomination w/o comments etc.
    _creation_time: datetime.datetime | None  # Creation time of the nomination
    _days_old: int  # How many days passed since the nomination was created?
    _days_since_last_edit: int  # How many days passed since it was edited?
    _creator: str | None  # Username of the creator of the nominated image
    _uploader: dict[str, str]  # Mapping: filename -> username of uploader
    _nominator: str | None  # Username of the creator of the nomination
    _image_count: int | None  # Count of (full-size) images in the nomination
    _filename: str | None  # Name of the nominated image, empty if not found
    _alternative: str | None  # If there are alternatives: selected image name
    _set_files: list[str] | None  # Names of nominated images (for set noms)
    _pro: int  # Count of pro votes
    _con: int  # Count of contra votes
    _neu: int  # Count of neutral votes

    def __init__(self, page: pywikibot.Page, list_name: str) -> None:
        """
        Although this is an abstract base class, the initializer is used
        by the concrete subclasses.  It sets all instance variables
        to passed values and to default values resp.

        @param page      A pywikibot.Page object for the nomination subpage.
        @param list_name A string with the name of the candidate list page.
        """
        # Save passed values
        self._list_page_name = list_name
        self._page = page
        # Set other instance variables to default values
        self._filtered_content = None
        self._creation_time = None
        self._days_old = -1
        self._days_since_last_edit = -1
        self._creator = None
        self._uploader: dict[str, str] = {}
        self._nominator = None
        self._image_count = None
        self._filename = None
        self._alternative = None
        self._set_files = None
        self._pro = -1
        self._con = -1
        self._neu = -1

    @property
    def page(self) -> pywikibot.Page:
        """Simple property getter for the nomination subpage."""
        return self._page

    def print_all_info(self) -> None:
        """
        Print the name, status, vote counts and other information
        for this candidate, as part of an overview of all open candiates.
        """
        try:
            self.count_votes()
            out(
                f"{self.cut_title()}: "
                f"P:{self._pro:02d} "
                f"C:{self._con:02d} "
                f"N:{self._neu:02d} "
                f"Do:{self.days_old():02d} "
                f"De:{self.days_since_last_edit():02d} "
                f"Se:{self.section_count():02d} "
                f"Im:{self.image_count():02d} "
                f"W:{y_n(self.is_withdrawn() or self.is_fpx())} "
                f"S:{'?' if self.is_ignored() else y_n(self.is_passed())} "
                f"({self.status_string()})"
            )
        except pywikibot.exceptions.NoPageError:
            error(f"{self.cut_title()}: -- No such page --")

    def filtered_content(self) -> str:
        """
        Return a filtered version of the wikitext of the nomination subpage,
        i.e. without comments, stricken text, code examples, etc.
        The filtered version is cached because we need it very often.
        """
        if self._filtered_content is None:
            self._filtered_content = filter_content(
                self._page.get(get_redirect=False)
            )
        return self._filtered_content

    def reset_filtered_content(self) -> None:
        """Flag filtered content as outdated after changing page contents."""
        self._filtered_content = None

    def creator(self, link: bool) -> str:
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
            if match := CREATOR_NAME_REGEX.search(self.filtered_content()):
                self._creator = match.group(1).replace("_", " ").strip()
            else:
                self._creator = ""
        if self._creator and link:
            return user_page_link(self._creator)
        return self._creator

    def uploader(self, filename: str, link: bool) -> str:
        """
        Returns the name of the user who has uploaded the original version
        of the image; if link is True, returns a link to the user page.
        (This method works differently than nominator() because all files of
        a set must have the same nominator, but can have different uploaders.)
        """
        try:
            username = self._uploader[filename]
        except KeyError:
            username = oldest_revision_user(pywikibot.Page(_g_site, filename))
            self._uploader[filename] = username
        if username:
            return user_page_link(username) if link else username
        return "Unknown"

    def nominator(self, link: bool) -> str:
        """
        Returns the name of the user who has created the nomination;
        if link is True, returns a link to the nominator's user page.
        """
        if self._nominator is None:
            self._nominator = oldest_revision_user(self._page)
        if self._nominator:
            return user_page_link(self._nominator) if link else self._nominator
        return "Unknown"

    def is_set(self) -> bool:
        """
        Check whether this candidate is a set nomination or not;
        the name of the nomination subpage for a set must contain "/[Ss]et/".
        """
        return re.search(r"/ *[Ss]et */", self._page.title()) is not None

    def set_files(self) -> list[str]:
        """
        Try to return a list of all nominated files in a set nomination.
        We just search for all filenames in the first <gallery>...</gallery>
        on the nomination subpage.
        If we can't identify any files the result is an empty list.
        """
        # Use cached result if possible
        if self._set_files is not None:
            return self._set_files
        # Change default value from None (meaning 'not retrieved yet')
        # to an empty list (meaning 'files not OK'; used if we return early)
        self._set_files = []

        # Extract contents of the first <gallery>...</gallery> element
        subpage_name = self._page.title()
        cut_title = self.cut_title()
        match = re.search(
            r"<gallery[^>]*>(.+?)</gallery>",
            self.filtered_content(),
            flags=re.DOTALL,
        )
        if not match:
            error(f"{cut_title}: (Error: no <gallery> in set nomination)")
            ask_for_help(
                f"In the set nomination [[{subpage_name}]], the bot "
                "did not find the <code><nowiki><gallery></nowiki></code> "
                "element with the nominated images. "
                f"Perhaps the formatting is damaged. {PLEASE_FIX_HINT}"
            )
            return []
        text_inside_gallery = match.group(1)

        # First try to find files which are properly listed with 'File:'
        # or 'Image:' prefix; they must be the first element on their line,
        # but leading whitespace is tolerated:
        files_list = re.findall(
            r"^ *(?:[Ff]ile|[Ii]mage) *:([^\n|]+)",
            text_inside_gallery,
            flags=re.MULTILINE,
        )
        if not files_list:
            # If we did not find a single file, let's try a casual search
            # for lines which, ahem, seem to start with an image filename:
            files_list = re.findall(
                r"^ *([^|\n:<>\[\]]+\.(?:jpe?g|tiff?|png|svg|webp|xcf))",
                text_inside_gallery,
                flags=re.MULTILINE | re.IGNORECASE,
            )
        if not files_list:
            # Still no files found, so we must skip this candidate
            error(f"{cut_title}: (Error: found no images in set)")
            ask_for_help(
                f"The set nomination [[{subpage_name}]] seems to contain "
                "no images. Perhaps the formatting is damaged. "
                f"{PLEASE_FIX_HINT}"
            )
            return []

        # Format and check filenames, resolve any redirects
        for i, filename in enumerate(files_list, start=0):
            # Add (back) the 'File:' prefix (our search omits the prefixes)
            filename = f"{FILE_NAMESPACE}{filename.replace('_', ' ').strip()}"
            page = pywikibot.Page(_g_site, filename)
            if not page.exists():
                # File not found, skip this candidate
                error(
                    f"{cut_title}: (Error: can't find set image '{filename}')"
                )
                ask_for_help(
                    f"The set nomination [[{subpage_name}]] lists the image "
                    f"[[:{filename}]], but that image file does not exist. "
                    "Perhaps it has been renamed or deleted. "
                    f"{PLEASE_FIX_HINT}"
                )
                return []
            if page.isRedirectPage():
                try:
                    page = page.getRedirectTarget()
                except pywikibot.exceptions.PageRelatedError:
                    # Circular or invalid redirect etc., skip candidate
                    error(
                        f"{cut_title}: (Error: invalid redirect "
                        f"in set image '{filename}')"
                    )
                    ask_for_help(
                        f"The set nomination [[{subpage_name}]] lists "
                        f"the image [[:{filename}]], but the image page "
                        "contains a circular or invalid redirect. "
                        f"{PLEASE_FIX_HINT}"
                    )
                    return []
                if not page.exists():
                    # Broken redirect, skip candidate
                    error(
                        f"{cut_title}: (Error: broken redirect "
                        f"in set image '{filename}')"
                    )
                    ask_for_help(
                        f"The set nomination [[{subpage_name}]] lists "
                        f"the image [[:{filename}]], but the image page "
                        "redirects to a file or page which does not exist. "
                        f"{PLEASE_FIX_HINT}"
                    )
                    return []
                out(f"Resolved redirect: '{filename}' -> '{page.title()}'.")
                filename = page.title()  # Update filename.
            files_list[i] = filename

        # Save and return files list
        self._set_files = files_list
        return files_list

    def find_gallery_of_file(self) -> str:
        """
        Try to find the gallery link in the nomination subpage;
        this is used to copy the link to the results template.
        """
        match = re.search(
            r"Gallery[^\n]+?\[\[Commons:Featured[_ ]pictures\/([^\n\]]+)",
            self.filtered_content(),
        )
        if match is not None:
            return clean_gallery_link(match.group(1))
        else:
            return ""

    def count_votes(self) -> None:
        """Count all the votes for this nomination."""
        if self._pro > -1:
            return  # Votes are already counted.
        if text := self.filtered_content():
            self._pro = len(self._PRO_VOTE_REGEX.findall(text))
            self._con = len(self._CONTRA_VOTE_REGEX.findall(text))
            self._neu = len(self._NEUTRAL_VOTE_REGEX.findall(text))
        else:
            error(f"Error - '{self._page.title()}' has no real content")

    def is_withdrawn(self) -> bool:
        """Has the nomination been marked as withdrawn?"""
        return WITHDRAWN_REGEX.search(self.filtered_content()) is not None

    def is_fpx(self) -> bool:
        """Is the nomination marked with a {{FPX}} or {{FPD}} template?"""
        return FPX_FPD_REGEX.search(self.filtered_content()) is not None

    def rules_of_fifth_day(self) -> bool:
        """Check if any of the rules of the fifth day can be applied."""
        if self.days_old() < 5:
            return False

        # Rules of the fifth day don't apply to nominations with alternatives
        if self.image_count() > 1:
            return False

        self.count_votes()

        # First rule of the fifth day
        if self._pro <= 1:
            return True

        # Second rule of the fifth day
        if self._pro >= 10 and self._con == 0:
            return True

        # If we arrive here, no rule applies
        return False

    def close(self) -> None:
        """
        Check whether the nomination is finished and can be closed or not.
        If yes, add the provisional result to the nomination subpage.
        """
        subpage_name = self._page.title()
        cut_title = self.cut_title()

        # First make sure that the page actually exists
        if not self._page.exists():
            error(f"{cut_title}: (Error: no such page?!)")
            ask_for_help(
                LIST_INCLUDES_MISSING_SUBPAGE.format(
                    list=self._list_page_name, subpage=subpage_name
                )
            )
            return

        # Close a withdrawn or FPXed/FPDed nomination if at least one full day
        # has passed since the last edit
        if (withdrawn := self.is_withdrawn()) or self.is_fpx():
            old_enough = self.days_since_last_edit() > 0
            reason = "withdrawn" if withdrawn else "FPXed/FPDed"
            action = "closing" if old_enough else "but waiting a day"
            out(f"{cut_title}: {reason}, {action}")
            if old_enough:
                self.move_to_log(reason)
            return

        # Is the nomination still active?
        fifth_day = self.rules_of_fifth_day()
        if not self.is_done() and not fifth_day:
            out(f"{cut_title}: (still active, ignoring)")
            return

        # Is there any other reason not to close the nomination?
        try:
            filtered_text = self.filtered_content()
        except pywikibot.exceptions.PageRelatedError as exc:
            error(f"{cut_title}: (Error: is not readable)")
            ask_for_help(
                "The bot could not read the nomination subpage "
                f"[[{subpage_name}]]: {format_exception(exc)}. "
                f"{PLEASE_FIX_HINT}"
            )
            return
        if not filtered_text:
            error(f"{cut_title}: (Error: has no real content)")
            ask_for_help(
                f"The nomination subpage [[{subpage_name}]] "
                f"seems to be empty. {PLEASE_FIX_HINT}"
            )
            return
        if re.search(r"\{\{\s*FPC-closed-ignored.*\}\}", filtered_text):
            out(f"{cut_title}: (marked as ignored, so ignoring)")
            return
        if self._COUNTED_RES_REGEX.search(filtered_text):
            out(f"{cut_title}: (needs review, ignoring)")
            return
        if self._REVIEWED_RES_REGEX.search(filtered_text):
            out(f"{cut_title}: (already closed and reviewed, ignoring)")
            return

        # OK, we should close the nomination
        if self.image_count() <= 1:
            self.count_votes()
        old_text = self._page.get(get_redirect=False)
        new_text = old_text.rstrip() + "\n\n" + self.get_result_string()
        if self.image_count() <= 1:
            new_text = self.fix_heading(new_text)

        # Save the new text of the nomination subpage
        summary = self.get_close_edit_summary(fifth_day)
        commit(old_text, new_text, self._page, summary)
        self.reset_filtered_content()

    def fix_heading(self, text: str, value: str | None = None) -> str:
        """
        Appends a keyword -- '(not) featured', '(not) delisted' --
        for the result to the heading of the nomination subpage.
        Reports if the nomination does not start correctly with a heading.
        Returns the modified wikitext of the nomination subpage.

        @param text  The complete wikitext of the nomination subpage.
        @param value If specified as 'yes' or 'no' (the value of the 'featured'
            or 'delisted' parameter from the reviewed results template),
            the keyword is based on this value, otherwise we call is_passed().
        """
        # Determine the keyword
        match value:
            case "yes":
                success = True
            case "no":
                success = False
            case _:
                success = self.is_passed()
        keyword = self._SUCCESS_KEYWORD if success else self._FAIL_KEYWORD
        # Check if the nomination correctly starts with a level 3+ heading
        text = text.lstrip()  # Silently remove irritating whitespace.
        match = re.match(r"===(.+?)===", text)
        if not match:
            warn(
                f"Nomination '{self._page.title()}' does not start "
                f"with a heading; can't add '{keyword}' to the title."
            )
            ask_for_help(
                f"The nomination [[{self._page.title()}]] does not start with "
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
    def get_result_string(self) -> str:
        """
        Returns the results template to be added when closing a nomination.
        Must be implemented by the subclasses.
        """
        pass

    @abc.abstractmethod
    def get_close_edit_summary(self, fifth_day: bool) -> str:
        """
        Returns the edit summary to be used when closing a nomination.
        Must be implemented by the subclasses.

        @param fifth_day Is the nomination closed early because the Rules
        of the 5th day apply to it?
        """
        pass

    def creation_time(self) -> datetime.datetime:
        """
        Returns the time at which this nomination was created.
        If we can't determine the creation time, for example because
        the page has been moved without leaving a redirect etc.,
        we return the current time so that we ignore this nomination
        as too young.
        """
        if self._creation_time:
            return self._creation_time

        try:
            timestamp = self._page.oldest_revision["timestamp"]
        except pywikibot.exceptions.PageRelatedError:
            error(
                f"Could not ascertain creation time of '{self._page.title()}', "
                "returning now()"
            )
            return datetime.datetime.now(datetime.UTC)
        # MediaWiki timestamps are always stored in UTC,
        # but querying a revision timestamp still returns an offset-naive
        # pywikibot.Timestamp object.  Therefore we convert it right away
        # to an offset-aware datetime object in order to compare it
        # easily and correctly to offset-aware datetime objects:
        self._creation_time = timestamp.replace(tzinfo=datetime.UTC)

        # print "C:" + self._creation_time.isoformat()
        # print "N:" + datetime.datetime.now(datetime.UTC).isoformat()
        return self._creation_time

    def status_string(self) -> str:
        """Returns a short string describing the status of the candidate."""
        if reviewed := self.is_reviewed():
            return reviewed
        if self.is_withdrawn():
            return "Withdrawn"
        if self.is_fpx():
            return "FPXed/FPDed"
        if self.is_ignored():
            return "Ignored"
        if self.is_done() or self.rules_of_fifth_day():
            text = (
                self._SUCCESS_KEYWORD if self.is_passed()
                else self._FAIL_KEYWORD
            )
            return text.capitalize()
        return "Active"

    def days_old(self) -> int:
        """Find the number of days this nomination has existed."""
        if self._days_old != -1:
            return self._days_old

        delta = datetime.datetime.now(datetime.UTC) - self.creation_time()
        self._days_old = delta.days
        return self._days_old

    def days_since_last_edit(self) -> int:
        """
        Number of whole days since last edit

        If the value can not be found -1 is returned
        """
        if self._days_since_last_edit != -1:
            return self._days_since_last_edit

        try:
            timestamp = self._page.latest_revision["timestamp"]
        except pywikibot.exceptions.PageRelatedError:
            return -1
        # MediaWiki timestamps are always stored in UTC,
        # but querying a revision timestamp still returns an offset-naive
        # pywikibot.Timestamp object.  Therefore we convert it right away
        # to an offset-aware datetime object in order to compare it
        # easily and correctly to offset-aware datetime objects:
        last_edit = timestamp.replace(tzinfo=datetime.UTC)

        delta = datetime.datetime.now(datetime.UTC) - last_edit
        self._days_since_last_edit = delta.days
        return self._days_since_last_edit

    def is_done(self) -> bool:
        """
        Check whether the voting period for the nomination is over.
        NB: This method doesn't consider the rules of the fifth day,
        please use rules_of_fifth_day() for that purpose.
        """
        return self.days_old() >= 9

    def is_passed(self) -> bool:
        """
        Check whether the nomination is successful acc. to the current votes.
        NB: This method doesn't consider the age of the nomination,
        please check that with is_done() and rules_of_fifth_day().
        NB: This method cannot properly handle nominations with alternatives.
        """
        if self.is_withdrawn():
            return False
        self.count_votes()
        return self._pro >= 7 and (self._pro >= 2 * self._con)

    def is_reviewed(self) -> str | Literal[False]:
        """
        Returns a short string for use with status_string(),
        indicating whether the nomination has already been closed and reviewed
        or has been closed and counted, but is still waiting for the review;
        if neither the one nor the other applies, returns False.
        """
        text = self.filtered_content()
        if self._REVIEWED_RES_REGEX.search(text):
            return "Reviewed"
        if self._COUNTED_RES_REGEX.search(text):
            return "Counted"
        return False

    def is_ignored(self) -> bool:
        """Nominations with alternative images require manual counting."""
        return self.image_count() > 1

    def section_count(self) -> int:
        """Counts the number of sections in this nomination."""
        return len(SECTION_REGEX.findall(self.filtered_content()))

    def image_count(self) -> int:
        """
        Counts the number of images in this nomination.
        Ignores small images which are below a certain threshold
        as they probably are just inline icons and not alternatives.
        """
        if self._image_count is not None:
            return self._image_count
        images = IMAGES_REGEX.findall(self.filtered_content())
        count = len(images)
        if count >= 2:
            # We have several images, check if some of them are marked
            # as thumbnails or are too small to be counted
            for image_link, _ in images:
                if is_just_thumbnail(image_link):
                    count -= 1
        self._image_count = count
        return count

    def existing_results(self) -> list[tuple[str, ...]]:
        """
        Scan the nomination subpage of this candidate and try to find
        and parse the verified (reviewed) results of the nomination.
        Returns either an empty list (if the nomination was not closed,
        if the results were not verified yet, or if the results do not use
        one of the usual formats) or a list of tuples; if the nomination
        has been verified correctly it should contain just a single tuple.
        The length of the tuple varies, depending on the results format,
        but only the first four values are important for a comparison:
        [0] count of support votes,
        [1] count of oppose votes,
        [2] count of neutral votes,
        [3] ('yes'|'no'|'(not )?featured'|'(not )?delisted').
        """
        text = self.filtered_content()
        # Search first for result(s) using the new template-base format,
        # and if this fails for result(s) in the old text-based format:
        results = self._VERIFIED_RES_REGEX.findall(text)
        if not results:
            results = self._OBSOLETE_RES_REGEX.findall(text)
        return results

    def compare_result_to_count(self) -> None:
        """
        If there is an existing result we compare it to a new vote count
        made by this bot and check whether they match or not.
        This is useful to test the vote counting code of the bot
        and to find possibly incorrect old results.
        """
        # Check status and get old result(s)
        if self.is_withdrawn():
            out(f"{self.cut_title()}: (ignoring, was withdrawn)")
            return
        if self.is_fpx():
            out(f"{self.cut_title()}: (ignoring, was FPXed/FPDed)")
            return
        if self.image_count() > 1:
            out(f"{self.cut_title()}: (ignoring, contains alternatives)")
            return
        results = self.existing_results()
        if not results:
            out(f"{self.cut_title()}: (ignoring, has no results)")
            return
        if len(results) > 1:
            out(f"{self.cut_title()}: (ignoring, has several results)")
            return

        # We have exactly one old result, so recount the votes and compare
        old_result = results[0]
        old_success = old_result[3].lower() in {"yes", "featured", "delisted"}
        old_pro = int(old_result[0])
        old_con = int(old_result[1])
        old_neu = int(old_result[2])
        self.count_votes()
        if (
            self._pro == old_pro
            and self._con == old_con
            and self._neu == old_neu
            and old_success == self.is_passed()
        ):
            status = "OK"
        else:
            status = "FAIL"

        # Print result as list entry to console
        out(
            f"{self.cut_title()}: "
            f"P:{self._pro:02d}/{old_pro:02d} "
            f"C:{self._con:02d}/{old_con:02d} "
            f"N:{self._neu:02d}/{old_neu:02d} "
            f"S:{y_n(self.is_passed())}/{y_n(old_success)} "
            f"({status})"
        )

    def cut_title(self) -> str:
        """Returns a fixed width title for the nomination."""
        title = self.subpage_name(keep_prefix=False, keep_number=True)
        # We skip 'removal/', 'File:' etc., but 'Set/' is informative
        if self.is_set():
            title = f"Set/{title}"
        return title[0:50].ljust(50)

    def filename(self) -> str:
        """
        Return the filename of this candidate or, if we cannot identify
        the nominated image, an empty string.
        For set nominations, use set_files() instead.
        """
        # Try the selected alternative or a cached result first
        if self._alternative is not None:
            return self._alternative
        if self._filename is not None:
            return self._filename
        # Change default value from None (meaning 'not retrieved yet')
        # to an empty string (meaning 'file not OK'; used if we return early)
        self._filename = ""

        # Try to derive the filename from the name of the nomination subpage
        page: pywikibot.Page | None  # Help typecheckers.
        subpage_name = self._page.title()
        cut_title = self.cut_title()
        if match := PREFIX_REGEX.search(subpage_name):
            filename = subpage_name[match.end(0):]
            # Use standard 'File:' namespace and remove '/2' etc.
            filename = FILE_NAMESPACE + re.sub(r" */ *\d+ *$", "", filename)
            page = pywikibot.Page(_g_site, filename)
            if not page.exists():
                # Image page not found; try the 1st image in the nomination
                warn(
                    f"{cut_title}: (Did not find '{filename}', "
                    "trying first image...)"
                )
                page = self._first_real_image_in_nomination()
                if page is None:
                    error(f"{cut_title}: (Error: can't find image page)")
                    ask_for_help(
                        f"The nomination [[{subpage_name}]] is about "
                        f"the image [[:{filename}]], but that image file "
                        "does not exist. Perhaps the file has been renamed. "
                        f"{PLEASE_FIX_HINT}"
                    )
                    return ""
        else:
            # Bad nomination subpage name; try the 1st image in the nomination
            warn(
                f"{cut_title}: (Could not derive filename, "
                "trying first image...)"
            )
            page = self._first_real_image_in_nomination()
            if page is None:
                error(f"{cut_title}: (Error: bad nomination subpage name)")
                ask_for_help(
                    f"The name of the nomination subpage [[{subpage_name}]] "
                    "is irregular, therefore the bot cannot identify "
                    f"the nominated image. {PLEASE_FIX_HINT}"
                )
                return ""
        # If we arrive here, 'page' should point to a valid page.

        # Check if the image was renamed and try to resolve the redirect
        if page.isRedirectPage():
            filename = page.title()
            try:
                page = page.getRedirectTarget()
            except pywikibot.exceptions.PageRelatedError:
                # Circular or invalid redirect etc., skip candidate
                error(f"{cut_title}: (Error: invalid redirect)")
                ask_for_help(
                    f"The nomination [[{subpage_name}]] is about the image "
                    f"[[:{filename}]], but the image page contains "
                    f"an invalid redirect. {PLEASE_FIX_HINT}"
                )
                return ""
            if not page.exists():
                # Broken redirect, skip candidate
                error(f"{cut_title}: (Error: broken redirect)")
                ask_for_help(
                    f"The nomination [[{subpage_name}]] is about the image "
                    f"[[:{filename}]], but the image page redirects to a file "
                    f"or page which does not exist. {PLEASE_FIX_HINT}"
                )
                return ""
            out(f"Resolved redirect: '{filename}' -> '{page.title()}'.")

        # Use the official spelling from the file page
        self._filename = page.title()
        return self._filename

    def _first_real_image_in_nomination(self) -> pywikibot.Page | None:
        """
        Return a pywikibot.Page object for the first image file
        which is linked in reasonable size (not as a mere thumbnail)
        in the text of the nomination subpage and actually exists.
        If no such image file is found, return None.
        """
        images = IMAGES_REGEX.findall(self.filtered_content())
        for image_link, image_name in images:
            if not is_just_thumbnail(image_link):
                page = pywikibot.Page(_g_site, image_name)
                if page.exists():
                    return page
        return None

    def subpage_name(
        self,
        keep_prefix: bool = True,
        keep_number: bool = True,
    ) -> str:
        """
        Returns the name of the nomination subpage for this candidate
        without the leading 'Commons:Featured picture candidates/'
        (if you want to include it, just call 'self._page.title()' instead).

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
        name = self._page.title()
        name = name.replace("_", " ")
        name = re.sub(wikipattern(CAND_PREFIX), "", name, count=1).strip()
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

    def move_to_log(self, reason: str | None = None) -> None:
        """
        Remove this candidate from the list of current candidates
        and add it to the log for the current month.
        Should only be called on closed and verified candidates.

        This is the last step of the parking procedure for FP candidates
        as well as for delisting candidates.
        """
        subpage_name = self._page.title()
        why = f" ({reason})" if reason else ""

        # Find and read the log page for this month
        # (if it does not exist yet it is just created from scratch)
        now = datetime.datetime.now(datetime.UTC)
        year = now.year
        month = now.strftime("%B")  # Full local month name, here: English
        log_link = f"{CAND_LOG_PREFIX}{month} {year}"
        log_page = pywikibot.Page(_g_site, log_link)
        try:
            old_log_text = log_page.get(get_redirect=True)
        except pywikibot.exceptions.NoPageError:
            old_log_text = ""

        # Append nomination to the log page
        if re.search(wikipattern(subpage_name), old_log_text):
            # This can happen if the process has previously been interrupted.
            out(
                f"Skipping add in move_to_log() for '{subpage_name}', "
                "candidate is already in the log."
            )
        else:
            new_log_text = old_log_text + "\n{{" + subpage_name + "}}"
            summary = f"Added [[{subpage_name}]]{why}"
            commit(old_log_text, new_log_text, log_page, summary)

        # Remove nomination from the list of current nominations
        candidates_list_page = pywikibot.Page(_g_site, self._list_page_name)
        old_cand_text = candidates_list_page.get(get_redirect=True)
        pattern = r" *\{\{\s*" + wikipattern(subpage_name) + r"\s*\}\} *\n?"
        new_cand_text = re.sub(pattern, "", old_cand_text, count=1)
        if old_cand_text == new_cand_text:
            # This can happen if the process has previously been interrupted.
            out(
                f"Skipping remove in move_to_log() for '{subpage_name}', "
                "candidate not found in list."
            )
        else:
            summary = f"Removed [[{subpage_name}]]{why}"
            commit(old_cand_text, new_cand_text, candidates_list_page, summary)

    def park(self) -> None:
        """
        Check that the candidate has exactly one valid verified result,
        that the image file(s) exist and that there are no other obstacles.
        If yes, park the candidate -- i.e., if the nomination was successful,
        promote the new FP(s) or delist the former FP respectively;
        else, if it has failed, just archive the nomination.
        """
        subpage_name = self._page.title()
        cut_title = self.cut_title()

        # Check that the nomination subpage actually exists
        if not self._page.exists():
            error(f"{cut_title}: (Error: no such page?!)")
            ask_for_help(
                LIST_INCLUDES_MISSING_SUBPAGE.format(
                    list=self._list_page_name, subpage=subpage_name
                )
            )
            return

        # Withdrawn/FPXed/FPDed nominations are handled by close()
        if self.is_withdrawn():
            out(f"{cut_title}: (ignoring, was withdrawn)")
            return
        if self.is_fpx():
            out(f"{cut_title}: (ignoring, was FPXed/FPDed)")
            return

        # Look for verified results
        # (leaving out stricken or commented results which have been corrected)
        results = self._VERIFIED_RES_REGEX.findall(self.filtered_content())
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

        # Are the nominated images readily available?
        if self.is_set():
            if not self.set_files():
                # Could not find set images, error was already reported
                return
        elif not self.filename():
            # Could not identify nominated image, error already reported
            return

        # We should now have a candidate with verified result that we can park
        verified_result = results[0]
        success = verified_result[3]
        if success in {"yes", "no"}:
            # If the keyword has not yet been added to the heading, add it now
            old_text = self._page.get(get_redirect=False)
            new_text = self.fix_heading(old_text, success)
            if new_text != old_text:
                commit(old_text, new_text, self._page, "Fixed header")
                self.reset_filtered_content()
            # Park the candidate
            if success == "yes":
                self.handle_passed_candidate(verified_result)
            else:
                self.move_to_log(self._FAIL_KEYWORD)
        else:
            error(
                f"{cut_title}: (Error: invalid verified "
                f"success status '{success}')"
            )
            ask_for_help(
                f"The verified success status <code>{success}</code> "
                f"in the results template of [[{subpage_name}]] "
                f"is invalid. {PLEASE_FIX_HINT}"
            )

    @abc.abstractmethod
    def handle_passed_candidate(self, results: tuple[str, ...]) -> None:
        """
        Handle the parking procedure for a passed candidate.
        Must be implemented by the subclasses.
        """
        pass


class FPCandidate(Candidate):
    """A candidate up for promotion."""

    # Define class constants:
    # all class constants are inherited, no changes necessary.

    # Declare types of instance variables:
    # all instance variables are inherited, see superclass.

    # No __init__():
    # the class just uses the initializer of the superclass.

    def get_result_string(self) -> str:
        """
        Returns the results template to be added when closing a nomination.
        Implementation for FP candidates.
        """
        gallery = self.find_gallery_of_file()
        if self.image_count() > 1:
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
        # A simple FP nomination
        self.count_votes()
        return (
            "{{FPC-results-unreviewed"
            f"|support={self._pro}|oppose={self._con}|neutral={self._neu}"
            f"|featured={yes_no(self.is_passed())}"
            f"|gallery={gallery}"
            "|sig=~~~~}}"
        )

    def get_close_edit_summary(self, fifth_day: bool) -> str:
        """Implementation for FP candidates."""
        if self.image_count() > 1:
            return (
                "Closing for review - contains alternatives, "
                "needs manual counting"
            )
        # A simple FP nomination
        self.count_votes()
        return (
            f"Closing for review ({self._pro} support, "
            f"{self._con} oppose, {self._neu} neutral, "
            f"featured: {yes_no(self.is_passed())}, "
            f"5th day: {yes_no(fifth_day)})"
        )

    def handle_passed_candidate(self, results: tuple[str, ...]) -> None:
        """
        Promotes a new featured picture (or set of featured pictures):
        adds it to the appropriate gallery page, to the monthly overview
        and to the landing-page list of recent FPs,
        inserts the {{Assessments}} template into the description page(s),
        notifies nominator and uploader, etc.
        """
        subpage_name = self._page.title()
        cut_title = self.cut_title()

        # Some methods need the full gallery link with section anchor,
        # others only the gallery page name or even just the basic gallery.
        full_gallery_link = clean_gallery_link(results[4])
        gallery_page = re.sub(r"#.*", "", full_gallery_link).rstrip()
        if not gallery_page:
            error(f"{cut_title}: (ignoring, gallery not defined)")
            ask_for_help(
                f"The gallery link in the nomination [[{subpage_name}]] "
                f"is empty or broken. {PLEASE_FIX_HINT}"
            )
            return
        match = re.search(r"^(.*?)(?:/|$)", gallery_page)
        assert match is not None  # Regex matches always, help typecheckers.
        basic_gallery = match.group(1)

        # If there is more than one image, search for the selected alternative
        if self.image_count() > 1:
            if len(results) > 5 and results[5].strip():
                alternative = results[5].replace("_", " ").strip()
                if not pywikibot.Page(_g_site, alternative).exists():
                    error(
                        f"{cut_title}: (ignoring, specified alternative "
                        f"'{alternative}' not found)"
                    )
                    ask_for_help(
                        f"Cannot find the alternative [[:{alternative}]] "
                        f"specified by the nomination [[{subpage_name}]]. "
                        f"{PLEASE_FIX_HINT}"
                    )
                    self._alternative = ""  # Mark as invalid, like _filename.
                    return
                self._alternative = alternative
            else:
                error(f"{cut_title}: (ignoring, alternative not set)")
                ask_for_help(
                    f"The nomination [[{subpage_name}]] contains several "
                    "images, but does not specify the selected alternative. "
                    f"{PLEASE_FIX_HINT}"
                )
                return

        # Retrieve the image filename(s)
        if self.is_set():
            files = self.set_files()
        else:
            filename = self.filename()
            files = [filename] if filename else []
        if not files:
            # Could not identify the nominated image(s), error already reported
            return

        # Promote the new featured picture(s)
        self.add_to_featured_list(basic_gallery, files)
        self.add_to_gallery_page(full_gallery_link, files)
        self.add_assessments(files)
        self.add_assessment_to_media_info(files)
        self.add_to_current_month(files)
        self.notify_nominator(files)
        self.notify_uploader_and_creator(files)
        self.move_to_log(self._SUCCESS_KEYWORD)

    def add_to_featured_list(self, section_name: str, files: list[str]) -> None:
        """
        Adds the new featured picture to the list with recently
        featured images that is used on the FP landing page.
        Should only be called on closed and verified candidates.

        This is ==STEP 1== of the parking procedure.

        @param section_name The section name, like 'Animals' or 'Places'.
        (The list uses the basic part of the gallery links as section names.)
        @param files List with filename(s) of the featured picture or set.
        """
        filename = files[0]  # For set nominations just use the first file.

        # Read the list
        page = pywikibot.Page(_g_site, GALLERY_LIST_PAGE_NAME)
        try:
            old_text = page.get(get_redirect=False)
        except pywikibot.exceptions.PageRelatedError as exc:
            error(f"Error - can't read list of recent FPs: {exc}")
            fexc = format_exception(exc)
            ask_for_help(
                COULD_NOT_READ_RECENT_FPS_LIST.format(exception=fexc)
                + f" Then please add the new FP [[:{filename}]] "
                f"to the section ''{section_name}''."
            )
            return

        # Check if the image is already on the page.
        # This can happen if the process has previously been interrupted.
        if re.search(wikipattern(filename), old_text):
            out(
                f"Skipping add_to_featured_list() for '{filename}', "
                "image is already listed."
            )
            return

        # Find the correct section and its <gallery> element;
        # remove the last entry/entries from the <gallery> element,
        # keeping the 3 newest ones, and insert the new FP before them.
        esc_name = wikipattern(section_name)
        match = re.search(
            r"\n==\s*\{\{\{\s*\d+\s*\|\s*" + esc_name + r"\s*\}\}\}\s*==\s*"
            r"<gallery[^\n>]*>(.+?)</gallery>",
            old_text,
            flags=re.DOTALL,
        )
        if not match:
            error(f"Error - can't find gallery section '{section_name}'.")
            ask_for_help(
                "The bot could not add the new Featured picture "
                f"[[:{filename}]] to the list at [[{GALLERY_LIST_PAGE_NAME}]] "
                f"because it did not find the section ''{section_name}''. "
                "Either there is no subheading with that name, "
                "or it is not followed immediately by a valid "
                "<code><nowiki><gallery></nowiki></code> element. "
                "Please check whether the list page is OK or needs a fix, "
                "and add the new FP by hand to the correct section."
            )
            return
        entries = match.group(1).strip().splitlines()
        formatted = "\n".join(entry.strip() for entry in entries[:3])
        new_text = (
            old_text[:match.start(1)]
            + f"\n{filename}|{bare_filename(filename)}\n"
            + f"{formatted}\n"
            + old_text[match.end(1):]
        )

        # Commit the new text
        summary = f"Added [[{filename}]] to section '{section_name}'"
        commit(old_text, new_text, page, summary)

    def add_to_gallery_page(self, gallery_link: str, files: list[str]) -> None:
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
        subpage_name = self._page.title()

        # Split the gallery link into gallery page name and section anchor
        # (the latter can be empty)
        link_parts = gallery_link.split("#", maxsplit=1)
        gallery_page_name = link_parts[0].strip()
        section = link_parts[1].strip() if len(link_parts) > 1 else ""

        # Read the gallery page
        full_page_name = f"{FP_PREFIX}{gallery_page_name}"
        page = pywikibot.Page(_g_site, full_page_name)
        try:
            old_text = page.get(get_redirect=False)
        except pywikibot.exceptions.NoPageError:
            error(f"Error - gallery page '{full_page_name}' does not exist.")
            ask_for_help(
                f"The gallery page [[{full_page_name}]] which was specified "
                f"by the nomination [[{subpage_name}]] does not exist. "
                f"{PLEASE_CHECK_GALLERY_AND_SORT_FPS}"
            )
            return
        except pywikibot.exceptions.PageRelatedError as exc:
            error(f"Error - can't read gallery page '{full_page_name}': {exc}")
            ask_for_help(
                "The bot could not read the gallery page "
                f"[[{full_page_name}]] which was specified by "
                f"the nomination [[{subpage_name}]]: {format_exception(exc)}. "
                f"{PLEASE_CHECK_GALLERY_AND_SORT_FPS}"
            )
            return

        # Check if some of the new FPs are already on the page.
        # This can happen if the process has previously been interrupted.
        # We skip these files but handle any file which is not yet present.
        new_files = [
            file for file in files
            if not re.search(wikipattern(file), old_text)
        ]
        if not new_files:
            # Not a single file needs to be added, so we can stop here.
            out(
                f"Skipping add_to_gallery_page() for '{subpage_name}', "
                "image(s) already listed."
            )
            return
        # Format the new entries and a hint for the edit summary
        new_entries = "".join(
            f"{filename}|{bare_filename(filename)}\n"
            for filename in new_files
        )
        files_for_summary = f"[[{new_files[0]}]]"
        if len(new_files) > 1:
            files_for_summary += f" and {len(new_files) - 1} more set file(s)"

        # Search for the section to which we have to add the new FPs
        if insert_at := self._find_gallery_insertion_place(
            gallery_link, full_page_name, section, old_text
        ):
            summary = f"Added {files_for_summary} to section '{section}'"
        elif insert_at := self._find_unsorted_insertion_place(
            full_page_name, old_text
        ):
            summary = f"Added {files_for_summary} to the 'Unsorted' section"
        else:  # Serious error with gallery page, already reported
            return

        # Add the new FP(s) to the gallery page
        new_text = (
            f"{old_text[:insert_at.start]}\n"
            + new_entries
            + old_text[insert_at.stop:]
        )
        commit(old_text, new_text, page, summary)

    def _find_gallery_insertion_place(
        self,
        gallery_link: str,
        full_page_name: str,
        section: str,
        old_text: str,
    ) -> slice | None:
        """
        Search for the start of the <gallery>...</gallery> element
        of the section to which we have to add the new featured picture(s).

        Returns:
        If successful, a slice object describing the index values
        of the characters which should be replaced by the new entries;
        or None if we did not find a valid target section and have to use
        the 'Unsorted' section instead.
        """
        subpage_name = self._page.title()
        unsorted_hint = ADDING_FPS_TO_UNSORTED_SECTION.format(
            page=full_page_name
        )

        # Have we got a section anchor?
        if not section:
            # There was no section anchor
            warn("No section anchor, adding FP(s) to 'Unsorted' section.")
            ask_for_help(
                f"The gallery link ''{gallery_link}'' in the nomination "
                f"[[{subpage_name}]] does not specify a gallery section. "
                f"{unsorted_hint}"
            )
            return None

        # Search for the subheading matching the section anchor
        match = re.search(
            r"\n=+ *" + re.escape(section) + r" *=+(?: *\n)+", old_text
        )
        if not match:
            warn(
                "Found no matching subheading, "
                "adding FP(s) to 'Unsorted' section."
            )
            ask_for_help(
                f"The section anchor ''{section}'' in the gallery link "
                f"of the nomination [[{subpage_name}]] does not match "
                f"any subheading on the gallery page [[{full_page_name}]] "
                f"letter for letter. {unsorted_hint}"
            )
            return None

        # Check if that subheading opens a valid target section,
        # i.e., whether it is directly followed by the associated
        # <gallery>...</gallery> element or not
        match = ASSOC_GALLERY_ELEMENT_REGEX.match(old_text, pos=match.end(0))
        if not match:
            warn(
                "Subheading is not a valid target, "
                "adding FP(s) to 'Unsorted' section."
            )
            ask_for_help(
                f"The gallery link ''{gallery_link}'' "
                f"in the nomination [[{subpage_name}]] "
                f"points to a heading on [[{full_page_name}]], "
                f"but [[{full_page_name}#{section}|that heading]] "
                "is not a valid target because it is not followed "
                "immediately by an associated "
                "<code><nowiki><gallery></nowiki></code> element. "
                "Perhaps this is a superordinate heading and the "
                "image should be added to one of its subsections; "
                f"but to which one? {unsorted_hint}"
            )
            return None

        # If we arrive here, we have found a valid target section.
        # Check if that section is just the 'Unsorted' section
        # (this actually happens; it's valid, but not helpful,
        # so we handle the request, but also ask for help).
        if section == UNSORTED_HEADING:
            warn("Gallery link points to 'Unsorted' section.")
            ask_for_help(
                f"The gallery link ''{gallery_link}'' in the nomination "
                f"[[{subpage_name}]] instructs the bot to put the new "
                "featured picture(s) into the ''Unsorted'' section "
                f"of [[{full_page_name}]]. That is not exactly helpful "
                "because this section is used only for images "
                "which need to be sorted into a more specific section. "
                "So please move the new featured picture(s) "
                "to a more appropriate place."
            )
        return slice(match.end(1), match.end(0))

    def _find_unsorted_insertion_place(
        self,
        full_page_name: str,
        old_text: str,
    ) -> slice | None:
        """Search for the start of the <gallery>...</gallery> element
        of the 'Unsorted' section in order to insert the new FP(s);
        it should be just the last <gallery> element on the gallery page.

        Returns:
        If successful, a slice object describing the index values
        of the characters which should be replaced by the new entries;
        or None if we did not even find a usabale 'Unsorted' section.
        """
        if (start := old_text.rfind("<gallery")) >= 0:
            if match := GALLERY_ENTRY_START_REGEX.search(old_text, pos=start):
                return slice(match.start(0) + 1, match.end(0))
        error(
            "Error - found no 'Unsorted' section on "
            f"'{full_page_name}', can't add new FP(s)."
        )
        ask_for_help(
            f"The gallery page [[{full_page_name}]] which was "
            f"specified by the nomination [[{self._page.title()}]] "
            "seems to be invalid or broken. The bot did not find "
            "a valid <code><nowiki><gallery></nowiki></code> element "
            "for the ''Unsorted'' section on that page. "
            "Either there is no such section or the formatting of its "
            "<code><nowiki><gallery></nowiki></code> element is damaged. "
            f"{PLEASE_CHECK_GALLERY_AND_SORT_FPS}"
        )
        return None

    def add_assessments(self, files: list[str]) -> None:
        """
        Adds the {{Assessments}} template to the description page
        of a featured picture, resp. to all files in a set.
        Should only be called on closed and verified candidates.

        This is ==STEP 3== of the parking procedure.

        @param files List with filename(s) of the featured picture or set.
        """
        subpage_name = self.subpage_name(keep_prefix=False, keep_number=True)
        for filename in files:
            # Try to get and read the image description page
            page = pywikibot.Page(_g_site, filename)
            try:
                old_text = page.get(get_redirect=False)
            except pywikibot.exceptions.PageRelatedError as exc:
                # If the image has been deleted etc., we must just ignore it
                error(f"Error - can't read '{filename}': {exc}")
                continue

            # Search and (if found) update the {{Assessments}} template
            found, up_to_date, new_text = update_assessments_template(
                old_text, 1, subpage_name
            )
            if found:
                if up_to_date:
                    # Old and new template are identical, so skip this file,
                    # but continue to check other files (for set nominations)
                    out(
                        f"Skipping add_assessments() for '{filename}', "
                        "image is already featured."
                    )
                    continue
                # Else: The {{Assessments}} template was found and updated.
            else:
                # There is no {{Assessments}} template, so just add a new one.
                tmpl = f"{{{{Assessments|featured=1|com-nom={subpage_name}}}}}"
                # Search for the best location, in order of priority:
                # 1) At the very end of the file description stuff by putting
                # it right before the header of the license section;
                # 2) after the location templates (usually they appear after
                # the info templates and are displayed in unity with them);
                # 3) after one of the common information templates.
                if match := re.search(
                    r"\n== *\{\{ *int:license-header *\}\} *==", old_text
                ):
                    end = match.start(0)
                else:
                    end = find_end_of_template(
                        old_text, r"(?:[Oo]bject[ _])?[Ll]ocation(?:[ _]dec)?"
                    )
                    if not end:
                        end = find_end_of_template(
                            old_text,
                            r"[Ii]nformation|[Aa]rtwork"
                            r"|[Pp]hotograph|[Aa]rt[ _][Pp]hoto",
                        )
                if end:
                    # Use no empty line before, 1 empty line after the template
                    new_text = (
                        f"{old_text[:end].rstrip()}\n"
                        f"{tmpl}\n"
                        "\n"
                        f"{old_text[end:].lstrip()}"
                    )
                else:
                    # Searches have failed, just add the template at the top
                    new_text = f"{tmpl}\n\n{old_text.lstrip()}"

            # Commit the new text
            try:
                commit(old_text, new_text, page, "FP promotion")
            except pywikibot.exceptions.LockedPageError:
                error(
                    f"Error - image '{filename}' is locked, "
                    "can't add/update {{Assessments}}."
                )

    def add_assessment_to_media_info(self, files: list[str]) -> None:
        """
        Adds the 'Commons quality assessment' (P6731) claim
        'Wikimedia Commons featured picture' (Q63348049)
        to the Media Info (structured data) of the new featured picture,
        resp. of all files in a successful set nomination.

        This is ==STEP 4== of the parking procedure.

        @param files List with filename(s) of the featured picture or set.
        """
        # As effective date for the FP status we use the last modification
        # of the nomination subpage, normally made by the closing user.
        # To limit the precision to the day/date, we use "'precision': 11"
        # (right now more precise values are not supported on Wikidata:
        # https://phabricator.wikimedia.org/T57755).  Therefore we must
        # literally nullify the time part of the timestamp,
        # else MediaWiki rejects the timestamp as 'Malformed input'.
        try:
            timestamp = self._page.latest_revision["timestamp"]
        except pywikibot.exceptions.PageRelatedError:
            timestamp = datetime.datetime.now(datetime.UTC)
        iso_timestamp = timestamp.strftime("%Y-%m-%dT00:00:00Z")

        # Prepare data for the FP assessment claim
        try:
            fp_claim_site = pywikibot.Site("wikidata", "wikidata")
        except pywikibot.exceptions.Error as exc:
            # Creating a claim requires a Wikidata Site object, but sometimes
            # creating it fails due to connection errors.  In this case we
            # report the error and skip this step of the parking procedure,
            # handing it over to manual handling by adding a request for help.
            error(f"Error - could not create Site object for Wikidata: {exc}")
            file_links = ", ".join(f"[[:{filename}]]" for filename in files)
            ask_for_help(
                "The bot could not add a Featured picture assessment claim "
                "to the Structured data of one or more new FP(s) because "
                "creating and connecting a Pywikibot <code>Site</code> object "
                f"for Wikidata has failed: {format_exception(exc)}. "
                "Please add the claim [[:wikidata:Special:EntityPage/P6731|"
                "Commons quality assessment (P6731)]]: "
                "[[:wikidata:Special:EntityPage/Q63348049|"
                "Wikimedia Commons featured picture (Q63348049)]] "
                f"to the Structured data of {file_links}."
            )
            return
        fp_claim_data = {
            "mainsnak": {
                "snaktype": "value",
                "property": "P6731",
                "datatype": "wikibase-item",
                "datavalue": {
                    "value": {
                        "entity-type": "item",
                        "numeric-id": 63348049,
                    },
                    "type": "wikibase-entityid",
                },
            },
            "type": "statement",
            "rank": "normal",
            "qualifiers": {
                "P580": [
                    {
                        "snaktype": "value",
                        "property": "P580",
                        "datatype": "time",
                        "datavalue": {
                            "value": {
                                "time": iso_timestamp,
                                "precision": 11,
                                "after": 0,
                                "before": 0,
                                "timezone": 0,
                                "calendarmodel":
                                "http://www.wikidata.org/entity/Q1985727",
                            },
                            "type": "time",
                        },
                    },
                ],
            },
            "qualifiers-order": ["P580"],
        }

        for filename in files:
            # Get the Media Info for the image
            file_page = pywikibot.FilePage(_g_site, title=filename)
            if not file_page.exists():
                error(f"Error - image '{filename}' not found.")
                continue
            media_info = file_page.data_item()
            structured_data = media_info.get(force=True)
            try:
                statements = structured_data["statements"]
            except KeyError:
                error(
                    "Error - no 'statements' entry in structured data "
                    f"for '{filename}'."
                )
                continue

            # Is there already a FP assessment claim?
            try:
                quality_assessments = statements["P6731"]
            except KeyError:
                # No 'Commons quality assessment' (P6731) claims at all
                claim_already_present = False
            else:
                for claim in quality_assessments:
                    if is_fp_assessment_claim(claim):
                        claim_already_present = True
                        break
                else:
                    # We did not leave the loop via 'break': claim not found
                    claim_already_present = False

            # Add the claim if necessary
            if claim_already_present:
                out(
                    "Skipping add_assessment_to_media_info() "
                    f"for '{filename}', FP assessment claim already present."
                )
            else:
                # We must use a new Claim instance with every file,
                # else Pywikibot raises a ValueError.
                fp_claim = pywikibot.page.Claim.fromJSON(
                    site=fp_claim_site, data=fp_claim_data
                )
                try:
                    commit_media_info_changes(
                        filename, media_info, [], [fp_claim]
                    )
                except pywikibot.exceptions.LockedPageError:
                    error(f"Error - '{filename}' is locked.")

    def add_to_current_month(self, files: list[str]) -> None:
        """
        Adds the candidate to the monthly overview of new featured pictures.
        Should only be called on closed and verified candidates.

        This is ==STEP 5== of the parking procedure.

        @param files List with filename(s) of the featured picture or set.
        """
        # For set nominations just use the first file
        filename = files[0]

        # Extract voting results
        if match := VERIFIED_RESULT_REGEX.search(self.filtered_content()):
            ws = match.group(1)
            wo = match.group(2)
            wn = match.group(3)
        else:
            error(f"Error - no verified result found in '{self._page.title()}'")
            ask_for_help(
                f"The nomination [[{self._page.title()}]] is closed, "
                "but does not contain a valid verified result. "
                f"{PLEASE_FIX_HINT}"
            )
            return

        # Get the current monthly overview page
        now = datetime.datetime.now(datetime.UTC)
        year = now.year
        month = now.strftime("%B")  # Full local month name, here: English
        monthpage = f"{CHRONO_ARCHIVE_PREFIX}{month} {year}"
        page = pywikibot.Page(_g_site, monthpage)
        try:
            old_text = page.get(get_redirect=True)
        except pywikibot.exceptions.NoPageError:
            old_text = ""

        if old_text:
            # First check if the image is already on the page.
            # This can happen if the process has previously been interrupted.
            if re.search(wikipattern(filename), old_text):
                out(
                    f"Skipping add_to_current_month() for '{filename}', "
                    "image is already listed."
                )
                return
            # Find the number of entries in the gallery
            if match := re.search(
                r"<gallery\b[^>]*>(\n.*)</gallery>",
                old_text,
                flags=re.DOTALL,
            ):
                # Because of the obligatory NL after '<gallery>' even
                # an empty gallery must yield a count of 1, as we need it.
                count = match.group(1).count("\n")
            else:
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
        if self.is_set():
            set_name = self.subpage_name(keep_prefix=False, keep_number=False)
            title = f"Set: {set_name} ({len(files)} files)"
            summary = f"Added set [[{self._page.title()}|{set_name}]]"
        else:
            title = bare_filename(filename)
            summary = f"Added [[{filename}]]"
        if creator_link := self.creator(link=True):
            creator_hint = f"created by {creator_link}, "
        else:
            creator_hint = ""
        uploader_link = self.uploader(filename, link=True)
        nominator_link = self.nominator(link=True)
        new_text = old_text.replace(
            "</gallery>",
            f"{filename}|[[{self._page.title()}|{count}]] '''{title}'''<br> "
            f"{creator_hint}"
            f"uploaded by {uploader_link}, "
            f"nominated by {nominator_link},<br> "
            f"{{{{s|{ws}}}}}, {{{{o|{wo}}}}}, {{{{n|{wn}}}}}\n"
            "</gallery>",
            1,
        )
        commit(old_text, new_text, page, summary)

    def notify_nominator(self, files: list[str]) -> None:
        """
        Add a FP promotion template to the nominator's talk page.
        Should only be called on closed and verified candidates.

        This is ==STEP 6== of the parking procedure.

        @param files List with filename(s) of the featured picture or set.
        """
        # Get and read nominator talk page
        talk_link = f"{USER_TALK_NAMESPACE}{self.nominator(link=False)}"
        talk_page = pywikibot.Page(_g_site, talk_link)
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

        subpage_name = self.subpage_name(keep_prefix=True, keep_number=True)
        if self.is_set():
            # Notifications for set nominations add a gallery to the talk page
            # and use a special template with an appropriate message.
            # Since August 2025 we use an improved version of the template.
            nomination_link = self._page.title()
            set_title = self.subpage_name(keep_prefix=False, keep_number=False)
            template = (
                f"{{{{FPpromotionSet2|{set_title}|subpage={subpage_name}}}}}"
            )
            # Check if there already is a promotion template on the talk page.
            # This can happen if the process has previously been interrupted.
            if re.search(wikipattern(template), old_text):
                out(
                    f"Skipping notify_nominator() for set '{set_title}', "
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
                '<gallery mode="packed-hover" heights="120px">\n'
                f"{entries}\n"
                "</gallery>\n"
                f"{template} /~~~~"
            )
            summary = f"FP promotion of set [[{nomination_link}|{set_title}]]"

        else:
            # Single FP nomination
            filename = files[0]
            template = f"{{{{FPpromotion|{filename}|subpage={subpage_name}}}}}"
            # Check if there already is a promotion template on the talk page.
            # This can happen if the process has previously been interrupted.
            if re.search(wikipattern(template), old_text):
                out(
                    f"Skipping notify_nominator() for '{filename}', "
                    f"promotion template is already present at '{talk_link}'."
                )
                return
            new_text = (
                f"{old_text.rstrip()}\n"
                "\n"
                "== FP Promotion ==\n"
                f"{template} /~~~~"
            )
            summary = f"FP promotion of [[{filename}]]"

        # Commit the new text
        try:
            commit(old_text, new_text, talk_page, summary)
        except pywikibot.exceptions.LockedPageError:
            warn(f"The user talk page '{talk_link}' is locked, {ignoring}")

    def notify_uploader_and_creator(self, files: list[str]) -> None:
        """
        Add a FP promotion template to the talk page of the uploader and
        (optionally) of the original creator.  (Sometimes the creator
        is different from the uploader, e.g. when we promote a variant
        of an image which has been retouched by another user.
        In this case we notify also the original creator, if possible.)
        Should only be called on closed and verified candidates.

        This is ==STEP 7== of the parking procedure.

        To understand this method and how it differs from notify_nominator(),
        please keep in mind that all files in a set nomination have the same
        nominator, but they may have been uploaded by different users.
        That's very unusual and discouraged by the current FPC rules,
        but the bot stills supports that special case.  Therefore this method
        handles the files one by one, unlike notify_nominator().
        (Theoretically we would also need to support different creators,
        but at least for now we extract the creator name from the nomination,
        therefore we can handle just a single creator per nomination.)

        @param files List with filename(s) of the featured picture or set.
        """
        ignored_pages: set[str] = set()
        redirects: dict[str, str] = {}  # Mapping: old page name -> new name
        nominator_name = self.nominator(link=False)
        creator_name = self.creator(link=False)
        for filename in files:
            # Check if nominator, uploader and creator are the same user,
            # to avoid adding two templates to the same talk page
            uploader_name = self.uploader(filename, link=False)
            if uploader_name != nominator_name:
                self._notify_uploader_or_creator(
                    filename, True, uploader_name, ignored_pages, redirects
                )
            else:
                out(
                    f"Skipping uploader notification for '{filename}', "
                    "uploader is identical to nominator."
                )
            if (
                creator_name
                and not is_same_user(creator_name, nominator_name)
                and not is_same_user(creator_name, uploader_name)
            ):
                self._notify_uploader_or_creator(
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

    def _notify_uploader_or_creator(
        self,
        filename: str,
        is_uploader: bool,
        username: str,
        ignored_pages: set[str],
        redirects: dict[str, str],
    ) -> None:
        """Subroutine which implements the uploader/creator notification."""
        if is_uploader:
            role = "uploader"
            tmpl_name = "FPpromotedUploader"
        else:
            role = "creator"
            tmpl_name = "FPpromotedCreator"
        ignoring = f"but ignoring since it's just the {role} notification."

        # Find and read the user talk page
        talk_link = f"{USER_TALK_NAMESPACE}{username}"
        if talk_link in ignored_pages:
            # Don't load or report undefined or locked talk pages twice
            return
        talk_link = redirects.get(talk_link, talk_link)
        talk_page = pywikibot.Page(_g_site, talk_link)
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
        subpage_name = self.subpage_name(keep_prefix=True, keep_number=True)
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
        summary = f"FP promotion of [[{filename}]]"
        try:
            commit(old_text, new_text, talk_page, summary)
        except pywikibot.exceptions.LockedPageError:
            warn(f"The user talk page '{talk_link}' is locked, {ignoring}")
            ignored_pages.add(talk_link)


class DelistCandidate(Candidate):
    """A delisting candidate."""

    # Define class constants
    # Adapt values for the needs of this class:
    _SUCCESS_KEYWORD = "delisted"
    _FAIL_KEYWORD = "not delisted"
    _PRO_VOTE_REGEX = DELIST_VOTE_REGEX
    _CONTRA_VOTE_REGEX = KEEP_VOTE_REGEX
    _NEUTRAL_VOTE_REGEX = NEUTRAL_VOTE_REGEX
    _COUNTED_RES_REGEX = DELIST_COUNTED_TEMPLATE_REGEX
    _REVIEWED_RES_REGEX = DELIST_REVIEWED_TEMPLATE_REGEX
    _VERIFIED_RES_REGEX = VERIFIED_DELIST_RESULT_REGEX
    _OBSOLETE_RES_REGEX = OBSOLETE_DELIST_RESULT_REGEX

    # Declare types of instance variables:
    # all instance variables are inherited, see superclass.

    # No __init__():
    # the class just uses the initializer of the superclass.

    def get_result_string(self) -> str:
        """
        Returns the results template to be added when closing a nomination.
        Implementation for delisting candidates.
        """
        if self.image_count() != 1 or self.is_set():
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
        self.count_votes()
        return (
            "{{FPC-delist-results-unreviewed"
            f"|delist={self._pro}|keep={self._con}|neutral={self._neu}"
            f"|delisted={yes_no(self.is_passed())}"
            "|sig=~~~~}}"
        )

    def get_close_edit_summary(self, fifth_day: bool) -> str:
        """Implementation for delisting candidates."""
        if self.image_count() != 1 or self.is_set():
            # A delist-and-replace or a set delisting nomination
            return (
                "Closing for review - looks like a delist-and-replace "
                "or set delisting nomination, needs manual counting"
            )
        # A simple delisting nomination
        self.count_votes()
        return (
            "Closing for review "
            f"({self._pro} delist, {self._con} keep, {self._neu} neutral, "
            f"delisted: {yes_no(self.is_passed())}, "
            f"5th day: {yes_no(fifth_day)})"
        )

    def handle_passed_candidate(self, results: tuple[str, ...]) -> None:
        """
        Handle the parking procedure for a passed delisting candidate:
        remove the image from FP gallery pages, mark it as delisted
        in the chronological archives, update the {{Assessents}} template
        and remove FP categories from the image description page, etc.
        """
        if self.image_count() != 1 or self.is_set():
            # Support for delist-and-replace nominations and set delisting
            # is yet to be implemented.  Therefore ask for help and abort.
            ask_for_help(
                "The bot is not yet able to handle delist-and-replace "
                "nominations or set delisting nominations. "
                "Therefore, please take care of the images "
                f"from the nomination [[{self._page.title()}]] "
                "and remove or replace them manually."
            )
            return
        filename = self.filename()
        if not filename:
            # Could not identify nominated image, error already reported
            return
        self.remove_from_featured_list(filename)
        self.remove_from_gallery_pages(filename, results)
        self.remove_assessments(filename)
        self.remove_assessment_from_media_info(filename)
        self.move_to_log(self._SUCCESS_KEYWORD)

    def remove_from_featured_list(self, filename: str) -> None:
        """
        Remove a delisted FP from the list with recently featured images
        that is used on the FP landing page.

        Usually this is not necessary.  Until August 2025, a comment said:
        'We skip checking the FP landing page with the newest FPs;
        the chance that the image is still there is very small,
        and even then that page will soon be updated anyway.'
        That's correct.  But some sections of the list are updated
        only very rarely (e.g. the 'Other lifeforms' section),
        so a delisted FP could stay there for years.  That would be bad,
        and removing a FP from the list is easy, so let's just do it.
        """
        # Read the list
        page = pywikibot.Page(_g_site, GALLERY_LIST_PAGE_NAME)
        try:
            old_text = page.get(get_redirect=False)
        except pywikibot.exceptions.PageRelatedError as exc:
            error(f"Error - can't read list of recent FPs: {exc}")
            fexc = format_exception(exc)
            ask_for_help(
                COULD_NOT_READ_RECENT_FPS_LIST.format(exception=fexc)
                + f" If the delisted FP [[:{filename}]] "
                "appears on that page, please remove it."
            )
            return

        # Remove the image, if present, from the list
        new_text, n = re.subn(
            r"\n[^\n]*" + wikipattern(filename) + r"[^\n]*",
            "",
            old_text,
        )
        if n == 0:
            out(
                f"Skipping remove_from_featured_list() for '{filename}', "
                "image not found in list."
            )
            return
        summary = f"Removed [[{filename}]] per [[{self._page.title()}]]"
        commit(old_text, new_text, page, summary)

    def remove_from_gallery_pages(
        self,
        filename: str,
        results: tuple[str, ...],
    ) -> None:
        """
        Remove a delisted FP from the FP gallery pages and mark its entry
        in the chronological archive as delisted.
        """
        nomination_link = self._page.title()
        fn_pattern = wikipattern(filename.replace(FILE_NAMESPACE, ""))
        file_page = pywikibot.FilePage(_g_site, title=filename)
        if not file_page.exists():
            error(f"Error - image '{filename}' not found.")
            return
        using_pages = file_page.using_pages(
            namespaces=["Commons"], filterredir=False
        )
        for page in using_pages:
            page_name = page.title()
            if not page_name.startswith(FP_PREFIX):
                # Any other page -- don't remove the image here, of course.
                continue
            try:
                old_text = page.get(get_redirect=False)
            except pywikibot.exceptions.PageRelatedError as exc:
                error(f"Error - could not read {page_name}: {exc}")
                continue
            if page_name.startswith(CHRONO_ARCHIVE_PREFIX):
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
                        f"{match.group(1).rstrip()}<br> "
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

    def remove_assessments(self, filename: str) -> None:
        """Remove FP status from the image description page."""
        # Get and read image description page
        image_page = pywikibot.Page(_g_site, filename)
        try:
            old_text = image_page.get(get_redirect=False)
        except pywikibot.exceptions.PageRelatedError as exc:
            error(f"Error - can't read '{filename}': {exc}")
            return
        subpage_name = self.subpage_name(keep_prefix=False, keep_number=True)

        # Search and (if found) update the {{Assessments}} template
        found, up_to_date, new_text = update_assessments_template(
            old_text, 2, subpage_name
        )
        if not found:
            error(f"Error - no {{{{Assessments}}}} found on '{filename}'.")
            return
        if up_to_date:
            # This can happen if the process has previously been interrupted.
            out(
                f"Skipping add_assessments() for '{filename}', "
                "image is already delisted."
            )
            return

        # Remove 'Featured pictures of/from/by ...' categories
        new_text = TOPICAL_FP_CATEGORY_REGEX.sub("", new_text)

        # Commit the new text
        summary = f"Delisted per [[{self._page.title()}]]"
        try:
            commit(old_text, new_text, image_page, summary)
        except pywikibot.exceptions.LockedPageError:
            error(
                f"Error - '{filename}' is locked, "
                "can't update {{Assessments}}."
            )

    def remove_assessment_from_media_info(self, filename: str) -> None:
        """
        Remove the 'Commons quality assessment' (P6731) claim
        'Wikimedia Commons featured picture' (Q63348049)
        from the Media Info (structured data) for the image.
        """
        # Get the Media Info for the image
        file_page = pywikibot.FilePage(_g_site, title=filename)
        if not file_page.exists():
            error(f"Error - image '{filename}' not found.")
            return
        media_info = file_page.data_item()
        structured_data = media_info.get(force=True)
        try:
            quality_assessments = structured_data["statements"]["P6731"]
        except KeyError:
            out(
                "Found no 'Commons quality assessment' (P6731) claims "
                f"for '{filename}'."
            )
            return

        # Search for the claim(s) to be removed
        # (normally there should be at most one FP claim, but I have seen
        # weird things, so handle multiple FP claims to be on the save side)
        claims_to_remove = []
        for claim in quality_assessments:
            if is_fp_assessment_claim(claim):
                claims_to_remove.append(claim)
        if claims_to_remove:
            try:
                commit_media_info_changes(
                    filename, media_info, claims_to_remove, []
                )
            except pywikibot.exceptions.LockedPageError:
                error(f"Error - '{filename}' is locked.")
        else:
            out(
                "Found no 'Wikimedia Commons featured picture' assessment "
                f"claim (Q63348049) for '{filename}'."
            )


# FUNCTIONS

def wikipattern(text: str) -> str:
    """
    Prepares a filename, page name etc. so that it can be used in a regex
    and that spaces and underscores are handled as interchangeable,
    as usual in MediaWiki filenames, page names etc.
    """
    return re.sub(r"(?:\\ |_)", r"[ _]", re.escape(text))


# If this assertion ever fails, re.escape() handles spaces differently now,
# so please update the regex in the function above.
assert re.escape(" ") == r"\ "


def out(
    text: str,
    newline: bool = True,
    date: bool = False,
    heading: bool = False,
) -> None:
    """Output information or status messages to the console or log."""
    if heading:
        text = f"<<lightblue>>{text}<<default>>"
    dstr = (
        f"{datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M:%S')}: "
        if date and not _g_log_no_time
        else ""
    )
    pywikibot.stdout(f"{dstr}{text}", newline=newline)


def warn(text: str, newline: bool = True) -> None:
    """
    Output a warning to the console or log.  We use this if something
    does not work as expected, but it's probably not necessary to take action.

    TODO: Consider to use pywikibot.warning() instead of pywikibot.stdout(),
    but first clarify whether any log settings need to be be changed
    on the server then.
    """
    pywikibot.stdout(f"<<lightyellow>>{text}<<default>>", newline=newline)


def error(text: str, newline: bool = True) -> None:
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


def find_candidates(list_page_name: str, delist: bool) -> list[Candidate]:
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
    page = pywikibot.Page(_g_site, list_page_name)
    try:
        old_text = page.get(get_redirect=False)
    except pywikibot.exceptions.PageRelatedError as exc:
        error(f"Error - can't read candidate list '{list_page_name}': {exc}.")
        ask_for_help(
            f"The bot could not read the candidate list [[{list_page_name}]]: "
            f"{format_exception(exc)}. {SERIOUS_PROBLEM_CHECK_PAGE}"
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
    match_pattern = _g_match_pattern.lower()
    candidates: list[Candidate] = []  # Needs type hint: list is invariant.
    redirects: list[tuple[str, str]] = []

    for _, subpage_name in subpage_entries:
        # Skip nominations which are not of the expected type
        if bool(re.search(r"/ *[Rr]emoval */", subpage_name)) != delist:
            continue
        # Skip nominations which do not match the '-match' argument
        if match_pattern:
            comparison_name = PREFIX_REGEX.sub("", subpage_name).lower()
            if match_pattern not in comparison_name:
                continue
        subpage: pywikibot.Page | None  # Help typecheckers.
        subpage = pywikibot.Page(_g_site, subpage_name)
        # Check if nomination exists (filter out damaged links)
        if not subpage.exists():
            error(f"Error - nomination '{subpage_name}' not found, ignoring.")
            ask_for_help(
                LIST_INCLUDES_MISSING_SUBPAGE.format(
                    list=list_page_name, subpage=subpage_name
                )
            )
            continue
        # Check for redirects and and resolve them
        subpage = _resolve_nomination_subpage_redirect(subpage, redirects)
        if subpage is None:
            # Error during resolving, ignore nomination
            continue
        # Rename subpages with bad title (space after namespace prefix, etc.)
        subpage = _rename_nomination_subpage_with_bad_title(subpage, redirects)
        if subpage is None:
            # Error during renaming, ignore nomination
            continue
        # OK, seems the nomination is fine -- append candidate object
        candidates.append(candidate_class(subpage, list_page_name))

    # If we have found any redirects, update the candidates page
    if redirects:
        new_text = old_text
        for old_name, new_name in redirects:
            new_text = new_text.replace(old_name, new_name, 1)
        summary = (
            f"Resolved {len(redirects)} redirect(s) to renamed nomination(s)"
        )
        commit(old_text, new_text, page, summary)
    return candidates


def _resolve_nomination_subpage_redirect(
    subpage: pywikibot.Page,
    redirects: list[tuple[str, str]],
) -> pywikibot.Page | None:
    """Check if a nomination subpage contains a redirect and try to resolve it.

    Returns:
    If successful, a pywikibot.Page object of the real nomination subpage;
    if an error occurs, returns None.
    """
    if subpage.isRedirectPage():
        old_name = subpage.title()
        try:
            subpage = subpage.getRedirectTarget()
        except pywikibot.exceptions.PageRelatedError:
            # Circular or invalid redirect etc.
            error(
                "Error - invalid nomination redirect page "
                f"'{old_name}', ignoring."
            )
            ask_for_help(
                f"The nomination subpage [[{old_name}]] "
                f"contains an invalid redirect. {PLEASE_FIX_HINT}"
            )
            return None
        new_name = subpage.title()
        out(f"Nomination '{old_name}' has been renamed to '{new_name}'")
        redirects.append((old_name, new_name))
    return subpage


def _rename_nomination_subpage_with_bad_title(
    subpage: pywikibot.Page,
    redirects: list[tuple[str, str]],
) -> pywikibot.Page | None:
    """Check if a nomination subpage has a bad title which causes problems.
    If yes, try to rename the subpage; if we cannot remedy the problem,
    report it on the FPC talk page.

    Returns:
    If successful, a pywikibot.Page object with the renamed subpage;
    if an error occurs, returns None.
    """
    old_name = subpage.title()
    new_name = re.sub(r" */ *(?:[Ff]ile|[Ii]mage) *: *", "/File:", old_name)
    new_name = re.sub(r" */ *[Ss]et */ *", "/Set/", new_name)
    new_name = re.sub(r" */ *[Rr]emoval */ *", "/removal/", new_name)
    if new_name != old_name:
        if pywikibot.Page(_g_site, new_name).exists():
            error(
                "Error - could not fix name of nomination subpage "
                f"'{old_name}', ignoring."
            )
            ask_for_help(
                f"The nomination subpage [[{old_name}]] has a tricky name: "
                "it contains irritating spaces around the namespace prefix "
                "etc., or uses the old <code>Image:</code> namespace. "
                "This can confuse the {{tl|Assessments}} template and "
                "similar tools, and does not help the FP maintainers. "
                f"The bot tried to rename the subpage to [[{new_name}]], "
                "but there is already a subpage with that name. "
                f"{PLEASE_RENAME_HINT.format(subpage=old_name)}"
            )
            return None
        out(f"\nAbout to rename nomination '{old_name}' to '{new_name}':")
        if _confirm_changes(old_name, summary=NOMINATION_SUBPAGE_RENAMED):
            subpage = subpage.move(
                new_name,
                reason=NOMINATION_SUBPAGE_RENAMED,
                noredirect=False,  # Avoid problems with any links.
            )
            out(f"Renamed nomination '{old_name}' to '{new_name}'.")
            redirects.append((old_name, new_name))
            # Update all self-links (and all links to the nominated image)
            # in the nomination subpage
            old_text = subpage.get(get_redirect=False)
            new_text = old_text.replace(old_name, new_name)
            new_text = re.sub(
                r"(\[\[:?|/) *(?:[Ff]ile|[Ii]mage) *: *", r"\1File:", new_text
            )
            if new_text != old_text:
                summary = "Updated links after renaming nomination subpage"
                commit(old_text, new_text, subpage, summary)
            # NB: If for any reason this update fails (or is not confirmed
            # in interactive mode) the self-links in the nomination still work
            # thanks to the redirect created above.
        else:
            out(f"Renaming of '{old_name}' ignored.")
    return subpage


def check_candidates(
    check: Callable[[Candidate], None],
    list_page_name: str,
    delist: bool,
    descending: bool = True,
) -> None:
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
    if _g_site is None:  # Test is also necessary to help typecheckers.
        error("Fatal error - _g_site not initialized, call main() first.")
        return
    if not _g_site.logged_in():
        _g_site.login()

    # Find all current candidates
    candidates = find_candidates(list_page_name, delist)
    if not candidates:
        out(
            f"Found no {'delist' if delist else 'FP'} candidates"
            f"{' matching the -match argument' if _g_match_pattern else ''}."
        )
        return
    if descending:
        candidates.reverse()

    # Handle each candidate with the specified method
    total = len(candidates)
    for i, candidate in enumerate(candidates, start=1):
        if not _g_threads:
            out(f"({i:03d}/{total:03d}) ", newline=False, date=True)

        try:
            if _g_threads:
                while threading.active_count() >= config.max_external_links:
                    time.sleep(0.1)
                thread = ThreadCheckCandidate(candidate, check)
                thread.start()
            else:
                check(candidate)
        except pywikibot.exceptions.NoPageError as exc:
            error(f"Error - no such page: '{exc}'")
            ask_for_help(
                "The bot could not find a page (perhaps it has been renamed "
                f"without leaving a redirect): {format_exception(exc)}. "
                f"{SERIOUS_PROBLEM_CHECK_PAGE}"
            )
        except pywikibot.exceptions.LockedPageError as exc:
            error(f"Error - page is locked: '{exc}'")
            ask_for_help(
                "The bot could not edit a page because it is locked: "
                f"{format_exception(exc)}. {SERIOUS_PROBLEM_CHECK_PAGE}"
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Report exception with stack trace on the FPC talk page
            stack_trace = traceback.format_exc().rstrip()
            stack_trace = re.sub(  # Abbreviate file paths to filenames
                r'(File ").+?/([^/\n]+\.py")', r"\1\2", stack_trace
            )
            try:
                subpage_link = f"[[{candidate.page.title()}]]"
            except Exception:  # pylint: disable=broad-exception-caught
                subpage_link = f"the invalid nomination no. {i}"
            ask_for_help(
                f"The bot has stopped at {subpage_link} "
                "because of an uncaught exception:\n"
                f"<pre>{stack_trace}</pre>\n"
                "Developers, please look into this."
            )
            # Raise the exception again to enable normal error logging
            raise exc

        if _g_abort:
            break


def filter_content(text: str) -> str:
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


def strip_tag(text: str, tag: str) -> str:
    """Will simply take a tag and remove a specified tag."""
    return re.sub(r"(?s)<%s>.*?</%s>" % (tag, tag), "", text)


def clean_gallery_link(gallery_link: str) -> str:
    """
    Clean the gallery link: remove leading/trailing whitespace,
    replace underscores and non-breaking spaces by plain spaces
    (underscores are present if users just copy the link,
    a NBSP can be entered by accident with some keyboard settings),
    and replace %-encoded characters by their plain text counterparts.
    """
    link = gallery_link.replace("_", " ").replace("\u00A0", " ").strip()
    if "%" in link:
        try:
            link = urllib.parse.unquote(link, errors="strict")
        except UnicodeError:
            # Keep the encoded value of the link, just log the error
            error("Error - invalid %xx escape in gallery link.")
    return link


def bare_filename(filename: str) -> str:
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


def is_just_thumbnail(image_link: str) -> bool:
    """Is the image link from a nomination subpage just a thumbnail or icon?"""
    if IMAGE_THUMB_REGEX.search(image_link):
        return True
    size = IMAGE_SIZE_REGEX.search(image_link)
    if size and (int(size.group(1)) <= 150):
        return True
    return False


def yes_no(value: bool) -> str:
    """Translates a boolean value to 'yes' and 'no' resp."""
    return "yes" if value else "no"


def y_n(value: bool) -> str:
    """Translates a boolean value to 'Y' and 'N' resp., for use in tables."""
    return "Y" if value else "N"


def user_page_link(username: str) -> str:
    """Returns a link to the user page of the user."""
    return f"[[{USER_NAMESPACE}{username}|{username}]]"


def is_same_user(username_1: str, username_2: str) -> bool:
    """
    Are the two usernames effectively identical?

    The Mediawiki software handles the first character of page names
    and user names (after the namespace prefix) case-insensitively,
    but the remaining characters case-sensitively.  We must imitate
    this behaviour in order to compare usernames accurately.

    @param username_1: string with first username.
    @param username_2: string with second username.
    """
    return (
        username_1[0].upper() == username_2[0].upper()
        and
        username_1[1:] == username_2[1:]
    )


def format_exception(exc: Exception) -> str:
    """Format an exception nicely in order to use it in requests for help."""
    # Pywikibot exception messages often (but not always) end with '.'
    message = str(exc).strip().rstrip(".")
    name = type(exc).__name__
    return f"''{message}'' (<code>{name}</code>)"


def is_fp_assessment_claim(claim: pywikibot.page.Claim) -> bool:
    """
    Does the Pywikibot page.Claim object 'claim' represent a
    'Commons quality assessment' (P6731) claim with the value
    'Wikimedia Commons featured picture' (Q63348049)?
    """
    # For now a simple string comparison seems sufficient.
    # If this fails because of format variations etc.,
    # use a regex comparison or explore the nested data values.
    plain = repr(claim)
    return (
        "'property': 'P6731'" in plain
        and "'numeric-id': 63348049" in plain
    )


def oldest_revision_user(page: pywikibot.Page) -> str:
    """
    Returns the name of the user who has created the oldest (first) revision
    of a page on Wikimedia Commons; on errors just returns ''.

    @param page A pywikibot.Page object.
    """
    try:
        username = page.oldest_revision["user"]
        assert isinstance(username, str)  # Catch errors, help typecheckers.
        return username.strip()
    except (pywikibot.exceptions.PageRelatedError, AttributeError):
        return ""


def find_end_of_template(text: str, template_names: str) -> int:
    """
    Search for the end of a template.  Returns the position of the first
    character after the template, or 0 if the template is not found.
    We use a specific function because normal regexes as supported by
    Python's 're' module can't properly deal with nested templates.

    @param text           Wikitext of the page you want to search.
    @param template_names String with the allowed template name(s);
    handled as a regex fragment, so you can supply several names
    by separating them with '|' or by using '(?:...)?', etc.
    """
    match = re.search(r"(\{\{\s*" + template_names + r"\s*)[|{}]", text)
    if not match:
        return 0
    lvl = 0
    cp = match.end(1)

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


def update_assessments_template(
    old_text: str,
    featured_value: Literal[1, 2],
    com_nom_value: str,
) -> tuple[bool, bool, str]:
    """Update the {{Assessments}} template on an image description page,
    if present, to use the specified 'featured' and 'com-nom' values.
    This function is used both for FP and delisting candidates.
    It also updates the old 'subpage' parameter name to 'com-nom',
    but preserves the code formatting of the {{Assessments}} template
    because sometimes users format it with spaces, newlines, etc.

    Arguments:
    @param old_text: the text of the image description page.
    @param featured_value: new value for 'featured' parameter, 1 or 2.
    @param com_nom_value: new value for the 'com-nom' parameter.

    Returns a tuple, containing
    [0] bool: did the text contain an {{Assessments}} template?
    [1] bool: if there was a template, was it already up-to-date?
    [2] str: the updated text of the image description page.
    """
    if match := ASSESSMENTS_TEMPLATE_REGEX.search(old_text):
        params = match.group(1)
        # Search and update/append 'featured' parameter
        fstr = str(featured_value)
        if m := re.search(r"\|\s*featured\s*=\s*(\w+)", params):
            if m.group(1) != fstr:
                params = f"{params[:m.start(1)]}{fstr}{params[m.end(1):]}"
                after = m.start(1) + len(fstr)
            else:
                after = m.end(1)
        else:
            params += f"|featured={fstr}"
            after = len(params)
        # Search and update/append 'com-nom' parameter
        if m := re.search(
            # NB: The end of the regex is so complicated because we want
            # to leave any whitespace after the 'com-nom' value unchanged,
            # therefore it must be excluded from group 2.
            r"\|\s*(com-nom|subpage)\s*=\s*(.+?)\s*(?:$|[{}|\n])", params
        ):
            if m.group(1) == "subpage":
                # We can replace the old name directly because the length
                # of 'subpage' and 'com-nom' is identical
                params = f"{params[:m.start(1)]}com-nom{params[m.end(1):]}"
            if m.group(2) != com_nom_value:
                params = (
                    f"{params[:m.start(2)]}{com_nom_value}{params[m.end(2):]}"
                )
        else:
            # Insert new 'com-nom' right after the 'featured' parameter
            params = (
                f"{params[:after]}|com-nom={com_nom_value}{params[after:]}"
            )
        # Check and assemble result
        if params == match.group(1):
            return (True, True, old_text)
        new_text = (
            old_text[:match.start(1)]
            + params
            + old_text[match.end(1):]
        )
        return (True, False, new_text)
    return (False, False, old_text)


def ask_for_help(message: str) -> None:
    """
    Adds a short notice to the FPC talk page, asking for help with a problem.
    This is useful if the problem is very probably caused by a broken link,
    a wikitext syntax error, etc. on a Commons page, i.e. issues a normal
    human editor can correct easily.

    @param message A concise description of the problem in one or two
    short, but complete sentences; normally they should end with a request
    to change this or that in order to help the bot.
    """
    talk_page = pywikibot.Page(_g_site, FP_TALK_PAGE_NAME)
    try:
        old_text = talk_page.get()
    except pywikibot.exceptions.PageRelatedError:
        error(f"Error - could not read FPC talk page '{FP_TALK_PAGE_NAME}'.")
    if message in old_text:
        return  # Don't post the same message twice.
    new_text = old_text.rstrip() + (
        f"\n\n== {BOT_NAME} asking for help ==\n"
        f"[[File:Robot icon.svg|64px|left|link={USER_NAMESPACE}{BOT_NAME}]]\n"
        f"{message} Thank you! / ~~~~"
    )
    commit(old_text, new_text, talk_page, "Added request for help")


def _confirm_changes(page_name: str, summary: str | None = None) -> bool:
    """
    Subroutine with shared code for asking whether the proposed changes
    should be saved or discarded.

    @param page_name: Name (title) of the Wikimedia Commons page.
    @param summary:   Optional string with the edit summary;
        omit if there is no custom edit summary, i.e. for Media Info
        (structured data) changes.

    Returns:
    True if changes should be saved, False if changes should be discarded.
    (If the user decides to quit, we quit immediately, no return value.)
    """
    if _g_dry:
        return False
    if _g_auto:
        return True
    choice = pywikibot.bot.input_choice(
        f"Do you want to accept these changes to '{page_name}'"
        + (f" with summary '{summary}'?" if summary else "?"),
        [("yes", "y"), ("no", "n"), ("quit", "q")],
        automatic_quit=False,
    )
    match choice:
        case "y":
            return True
        case "n":
            return False
        case "q":
            out("Aborting.")
            sys.exit()
        case _:
            error("Congrats, you found a bug in pywikibot.bot.input_choice().")
            sys.exit()


def commit(
    old_text: str,
    new_text: str,
    page: pywikibot.Page,
    summary: str,
) -> None:
    """
    This will commit the new text of the page.
    Unless running in automatic mode it first shows the diff and asks
    whether the user accepts the changes or not.

    @param old_text Old text of the page, used to show the diff.
    @param new_text New text of the page to be submitted.
    @param page     Pywikibot Page object for the concerned Commons page.
    @param summary  The edit summary for the page history.
    """
    # Show the diff
    page_name = page.title()
    out(f"\nAbout to commit changes to '{page_name}':")
    lines_of_context = 0 if (_g_auto and not _g_dry) else 3
    pywikibot.showDiff(
        old_text,
        new_text,
        context=lines_of_context,
    )

    # Decide whether to save the changes
    if _confirm_changes(page_name, summary):
        page.put(new_text, summary=summary, watch=None, minor=False)
    else:
        out(f"Changes to '{page_name}' ignored.")


def commit_media_info_changes(
    filename: str,
    media_info: pywikibot.MediaInfo,
    claims_to_remove: list[pywikibot.page.Claim],
    claims_to_add: list[pywikibot.page.Claim],
) -> None:
    """
    When changing the Media Info (structured data) for an image,
    we cannot use the normal commit mechanism.
    So we print kind of a self-made diff; in interactive mode we ask
    whether to save the changes or not; if yes, we apply the changes.

    @param filename:         Name of the affected image file.
    @param media_info:       A Pywikibot MediaInfo instance representing
        the Media Info (structured data) for the image.
    @param claims_to_remove: list of Pywikibot Claim instances
        representing the statement(s) to be removed; can be empty.
    @param claims_to_add:    list of Pywikibot Claim instances
        representing the statement(s) to be added; can be empty.
    """
    assert claims_to_remove or claims_to_add

    # Show the diff
    out(f"\nAbout to change the Media Info (structured data) of '{filename}':")
    if claims_to_remove:
        removing = "- " + "\n- ".join(
            repr(claim) for claim in claims_to_remove
        )
        pywikibot.stdout(f"<<lightred>>{removing}<<default>>")
    if claims_to_add:
        adding = "+ " + "\n+ ".join(
            repr(claim) for claim in claims_to_add
        )
        pywikibot.stdout(f"<<lightgreen>>{adding}<<default>>")

    # Decide whether to save the changes
    if _confirm_changes(filename):
        # TODO: Whenever this is called a 2nd time or after another change,
        # I get a Pywikibot warning:
        #   'WARNING: API error badtoken: Invalid CSRF token.'
        # This appears to be harmless (the change is still saved),
        # but keep an eye on it.  Maybe related to Pywikibot bugs, cf. e.g.:
        #   https://phabricator.wikimedia.org/T261050
        if claims_to_remove:
            media_info.removeClaims(claims_to_remove)
        if claims_to_add:
            for claim in claims_to_add:
                media_info.addClaim(claim, bot=True)
        out(f"Media Info (structured data) of '{filename}' changed.")
    else:
        out(f"Changes to '{filename}' ignored.")


def main(*args: str) -> None:
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
    global _g_auto
    global _g_dry
    global _g_threads
    global _g_log_no_time
    global _g_match_pattern
    global _g_site

    # Define default values
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
    _g_site = pywikibot.Site()

    # First look for arguments which act as options for all tasks
    task_args = []
    i = 0
    while i < len(local_args):
        arg = local_args[i]
        match arg:
            case "-auto":
                _g_auto = True
            case "-dry":
                _g_dry = True
            case "-threads":
                _g_threads = True
            case "-delist":
                delist = True
            case "-fpc":
                fpc = True
            case "-notime":
                _g_log_no_time = True
            case "-match":
                # So the next argument must be the pattern string
                try:
                    _g_match_pattern = local_args[i + 1]
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
    if _g_threads and (not _g_dry and not _g_auto):
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
                    out("Recounting votes for delist candidates...", heading=True)
                    check_candidates(
                        Candidate.compare_result_to_count,
                        TEST_LOG_PAGE_NAME,
                        delist=True,
                        descending=False,
                    )
                if fpc:
                    out("Recounting votes for FP candidates...", heading=True)
                    check_candidates(
                        Candidate.compare_result_to_count,
                        TEST_LOG_PAGE_NAME,
                        delist=False,
                        descending=False,
                    )
            case "-close":
                if delist:
                    out("Closing delist candidates...", heading=True)
                    check_candidates(Candidate.close, CAND_LIST_PAGE_NAME, delist=True)
                if fpc:
                    out("Closing FP candidates...", heading=True)
                    check_candidates(Candidate.close, CAND_LIST_PAGE_NAME, delist=False)
            case "-info":
                if delist:
                    out("Gathering info about delist candidates...", heading=True)
                    check_candidates(Candidate.print_all_info, CAND_LIST_PAGE_NAME, delist=True)
                if fpc:
                    out("Gathering info about FP candidates...", heading=True)
                    check_candidates(Candidate.print_all_info, CAND_LIST_PAGE_NAME, delist=False)
            case "-park":
                if _g_threads and _g_auto:
                    warn("Auto-parking using threads is disabled for now...")
                    sys.exit()
                if delist:
                    out("Parking delist candidates...", heading=True)
                    check_candidates(Candidate.park, CAND_LIST_PAGE_NAME, delist=True)
                if fpc:
                    out("Parking FP candidates...", heading=True)
                    check_candidates(Candidate.park, CAND_LIST_PAGE_NAME, delist=False)
            case _:
                # This means we have forgotten to update the invalid_args test.
                error(
                    f"Error - unknown argument '{arg}'; aborting, see '-help'."
                )
                sys.exit()


def signal_handler(signal_number: int, frame: FrameType | None) -> None:
    """Handle a SIGINT (keyboard, Ctrl-C) interrupt."""
    global _g_abort
    print("\n\nReceived SIGINT, will abort...\n")
    _g_abort = True


# PROGRAM SETUP

# Install a custom handler for SIGINT (keyboard, Ctrl-C) interrupts
signal.signal(signal.SIGINT, signal_handler)

# Define the entry point for the bot program with the common idiom
if __name__ == "__main__":
    try:
        main()
    finally:
        pywikibot.stopme()
