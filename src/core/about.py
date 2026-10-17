"""Release status, backcountry safety wording, and Aegis family links."""

APP_NAME = "AegisHiker"
SUPPORT_URL = "https://yourprivacysw.com/contact.html"
SUPPORT_EMAIL = "support@yourprivacysw.com"
BETA = True

STATUS_SHORT = "Beta — free to use"

LICENSE_SUMMARY = (
    "AegisHiker is in beta and free to use: there is no license charge during the "
    "beta. In the future we may choose to limit some features unless a one-time license is "
    "purchased."
)

SAFETY = (
    "Hike safely: always carry a physical map and compass alongside your phone, leave a trip "
    "plan (Leave-Behind Safety Card) with a trusted contact, set a hard turnaround time before "
    "starting, pack the Ten Essentials, treat all backcountry water, and turn back immediately "
    "above treeline if thunderstorms or severe weather develop."
)

DISCLAIMER = (
    "Trail geometries, elevations, and condition flags are compiled from public-domain USGS / "
    "USFS / NPS sources and OpenStreetMap contributors (ODbL). Trail conditions change rapidly "
    "due to weather, rockfall, washouts, and seasonal closures — always verify current conditions "
    "with local rangers and exercise independent backcountry judgment."
)

PRIVACY = (
    "Your routes, GPS tracks, telemetry logs, and interactive hike maps stay in ~/.aegishiker on "
    "this computer. AegisPhotoVoyager's database is read locally in read-only mode to match "
    "photos taken along your hikes. The only network traffic is an optional regional pack download "
    "you explicitly request, and the local Wi-Fi sync server while transferring packs or tracks "
    "between this computer and your phone."
)


def window_title() -> str:
    return f"{APP_NAME} (Beta)" if BETA else APP_NAME


APP_AUTHOR = "Aegis Privacy Software (https://yourprivacysw.com)"
APP_LICENSE = STATUS_SHORT
APP_DESCRIPTION = f"{LICENSE_SUMMARY}\n\n{PRIVACY}"


def get_version() -> str:
    from . import paths

    return paths.app_version()


OTHER_APPS = [
    {
        "name": "aegisphotovoyager",
        "title": "Aegis Photo Voyager",
        "url": "https://yourprivacysw.com/apps/aegisphotovoyager/aegisphotovoyager.html",
        "blurb": "Privacy-focused photo management for professionals and enthusiasts: local AI search, "
                 "face recognition, maps, curation and sharing, all on your own machine.",
    },
    {
        "name": "aegisexpedition",
        "title": "Aegis Expedition",
        "url": "https://yourprivacysw.com/apps/aegisexpedition/aegisexpedition.html",
        "blurb": "Turns your AegisPhotoVoyager library into an interactive trip journal with GPS clustering, "
                 "local AI naming, and static website export.",
    },
    {
        "name": "aegiskitchencoach",
        "title": "Aegis Kitchen Coach",
        "url": "https://yourprivacysw.com/apps/aegiskitchencoach/aegiskitchencoach.html",
        "blurb": "Privacy-first kitchen companion: interviews you about what to cook, plans your week, "
                 "checks household allergies, and hands recipes to your phone over local Wi-Fi.",
    },
    {
        "name": "aegisbrewmaster",
        "title": "Aegis BrewMaster",
        "url": "https://yourprivacysw.com/apps/aegisbrewmaster/aegisbrewmaster.html",
        "blurb": "Private homebrewing: a conversation about the beer you want becomes a recipe, a guided brew "
                 "day with timers, tasting notes and printable sheets, with a local AI.",
    },
    {
        "name": "aegisretirementplanner",
        "title": "Aegis Retirement Planner",
        "url": "https://yourprivacysw.com/apps/aegisretirementplanner/aegisretirementplanner.html",
        "blurb": "Turns a conversation about the retirement you want into a budget, a bucket strategy and "
                 "Monte Carlo odds that the money lasts, with a local AI guide.",
    },
    {
        "name": "aegismoneyscope",
        "title": "Aegis MoneyScope",
        "url": "https://yourprivacysw.com/apps/aegismoneyscope/aegismoneyscope.html",
        "blurb": "Imports your bank and credit-card statements, categorizes every transaction and shows "
                 "where your money goes, with a local AI advisor.",
    },
    {
        "name": "aegisdvdbackup",
        "title": "Aegis DVD Backup",
        "url": "https://yourprivacysw.com/apps/aegisdvdbackup/aegidvdbackup.html",
        "blurb": "Copies DVDs to a folder on your computer as a playable VIDEO_TS folder or an exact ISO "
                 "image, and tells you honestly when a disc could not be fully read.",
    },
]
