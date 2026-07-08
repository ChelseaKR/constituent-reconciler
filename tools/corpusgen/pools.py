"""Name, address, and error-model data tables for the corpus generator.

Every name below is a common given or family name drawn from public
name-origin references, combined at random by the generator; no row this
package produces corresponds to a real person. ``NAME_CLASSES`` exists so R5
(bias by name and address class) has per-class ground truth to score recall
against — a small nonprofit's intake population is not uniformly Anglo names,
and a matcher tuned only on those will have blind spots the demo fixture (27
records, entirely Anglo and Vietnamese examples) cannot surface.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Name-origin classes -----------------------------------------------
# A label, not a claim about ethnicity or nationality: it groups names that
# share the error shapes this generator models (which transliteration
# variants exist, which nicknames are conventional). Kept coarse on purpose.
NAME_CLASSES: tuple[str, ...] = (
    "anglo",
    "hispanic",
    "east_asian",
    "south_asian",
    "vietnamese",
    "slavic",
    "arabic",
)

# first_name -> list of conventional nicknames/short forms in the same
# language community. Used for the "nickname" error channel.
NICKNAMES: dict[str, tuple[str, ...]] = {
    "Robert": ("Bob", "Rob", "Bobby"),
    "William": ("Bill", "Will", "Billy"),
    "Richard": ("Rick", "Dick", "Rich"),
    "James": ("Jim", "Jimmy"),
    "Elizabeth": ("Beth", "Liz", "Betty"),
    "Margaret": ("Peggy", "Meg", "Maggie"),
    "Katherine": ("Kathy", "Kate", "Katie"),
    "Michael": ("Mike", "Mikey"),
    "Jennifer": ("Jen", "Jenny"),
    "Christopher": ("Chris", "Topher"),
    "Patricia": ("Pat", "Patty", "Trish"),
    "Jonathan": ("Jon", "Jonny"),
    "Alexander": ("Alex", "Xander"),
    "Francisco": ("Paco", "Frank", "Cisco"),
    "Guadalupe": ("Lupe",),
    "Ignacio": ("Nacho",),
    "Rosario": ("Charo",),
    "Concepcion": ("Concha",),
    "Xiaoyan": ("Sherry",),
    "Weiming": ("Wei",),
    "Thi": ("Ty",),
    "Van": ("Vinny",),
    "Mohammed": ("Mo", "Hamid"),
    "Ibrahim": ("Abe",),
    "Aleksandr": ("Sasha", "Sanya"),
    "Yevgenia": ("Zhenya",),
}

# canonical spelling -> alternate transliteration/romanization spellings for
# the same underlying name. Used for the "transliteration" error channel.
TRANSLITERATIONS: dict[str, tuple[str, ...]] = {
    "Mohammed": ("Muhammad", "Mohamed", "Muhammed"),
    "Yousef": ("Yusuf", "Yousif", "Josef"),
    "Katarzyna": ("Katarzhyna", "Katazyna"),
    "Aleksandr": ("Alexander", "Aleksander"),
    "Yevgenia": ("Evgenia", "Yevgeniya"),
    "Nguyen": ("Nguyễn", "Nguyen"),
    "Thi": ("Thị", "Thi"),
    "Xiaoyan": ("Hsiao-yen", "Xiao Yan"),
    "Weiming": ("Wei-Ming", "Wei Ming"),
    "Hyun-woo": ("Hyunwoo", "Hyun Woo"),
    "Seung-min": ("Seungmin", "Seung Min"),
    "Chandrasekhar": ("Chandrashekar", "Chandrasekar"),
    "Priyanka": ("Priyangka", "Priyanca"),
    "Farid": ("Fareed", "Fareid"),
    "Zdenek": ("Zdeněk", "Zdenek"),
}

# surname -> alternate hyphenated / compound rendering. Used for the
# "hyphenation" error channel: one record spells a compound surname joined,
# the other hyphenated or with both parts intact.
COMPOUND_SURNAMES: dict[str, tuple[str, ...]] = {
    "Garcia Lopez": ("Garcia-Lopez", "GarciaLopez"),
    "Hernandez Cruz": ("Hernandez-Cruz", "HernandezCruz"),
    "Al Sayed": ("Al-Sayed", "Alsayed"),
    "Abdel Rahman": ("Abdel-Rahman", "AbdelRahman"),
    "Van Der Berg": ("Van-Der-Berg", "VanDerBerg"),
    "De La Cruz": ("De-La-Cruz", "DeLaCruz"),
    "Nguyen Tran": ("Nguyen-Tran", "NguyenTran"),
}


@dataclass(frozen=True)
class NamePool:
    """First and last names for one name-origin class."""

    name_class: str
    first_names: tuple[str, ...]
    last_names: tuple[str, ...]


NAME_POOLS: tuple[NamePool, ...] = (
    NamePool(
        "anglo",
        (
            "James",
            "Robert",
            "William",
            "Richard",
            "Elizabeth",
            "Margaret",
            "Katherine",
            "Michael",
            "Jennifer",
            "Christopher",
            "Patricia",
            "Jonathan",
            "Alexander",
            "Susan",
            "David",
            "Nancy",
        ),
        (
            "Smith",
            "Johnson",
            "Williams",
            "Brown",
            "Jones",
            "Davis",
            "Miller",
            "Wilson",
            "Anderson",
            "Taylor",
            "Thomas",
            "Moore",
        ),
    ),
    NamePool(
        "hispanic",
        (
            "Francisco",
            "Guadalupe",
            "Ignacio",
            "Rosario",
            "Concepcion",
            "Maria",
            "Jose",
            "Carmen",
            "Luis",
            "Ana",
            "Miguel",
            "Sofia",
        ),
        (
            "Garcia",
            "Rodriguez",
            "Martinez",
            "Hernandez",
            "Lopez",
            "Gonzalez",
            "Perez",
            "Sanchez",
            "Ramirez",
            "Torres",
            "Garcia Lopez",
            "Hernandez Cruz",
            "De La Cruz",
        ),
    ),
    NamePool(
        "east_asian",
        (
            "Xiaoyan",
            "Weiming",
            "Hyun-woo",
            "Seung-min",
            "Mei",
            "Jian",
            "Yuki",
            "Haruto",
            "Jin",
            "Ling",
        ),
        (
            "Chen",
            "Wang",
            "Li",
            "Zhang",
            "Liu",
            "Kim",
            "Park",
            "Lee",
            "Tanaka",
            "Yamamoto",
        ),
    ),
    NamePool(
        "south_asian",
        (
            "Chandrasekhar",
            "Priyanka",
            "Arjun",
            "Divya",
            "Rohan",
            "Anjali",
            "Karthik",
            "Meena",
        ),
        (
            "Patel",
            "Kumar",
            "Singh",
            "Sharma",
            "Khan",
            "Shah",
            "Reddy",
            "Gupta",
            "Rao",
        ),
    ),
    NamePool(
        "vietnamese",
        (
            "Thi",
            "Van",
            "Minh",
            "Anh",
            "Huong",
            "Tuan",
            "Linh",
            "Duc",
        ),
        (
            "Nguyen",
            "Tran",
            "Le",
            "Pham",
            "Hoang",
            "Vu",
            "Vo",
            "Dang",
            "Bui",
            "Do",
            "Nguyen Tran",
        ),
    ),
    NamePool(
        "slavic",
        (
            "Katarzyna",
            "Aleksandr",
            "Yevgenia",
            "Zdenek",
            "Piotr",
            "Marta",
            "Ivan",
            "Olga",
        ),
        (
            "Kowalski",
            "Nowak",
            "Petrov",
            "Ivanov",
            "Kovac",
            "Novak",
            "Horvat",
            "Wojcik",
        ),
    ),
    NamePool(
        "arabic",
        (
            "Mohammed",
            "Yousef",
            "Farid",
            "Layla",
            "Amina",
            "Karim",
            "Nadia",
            "Samir",
        ),
        (
            "Hassan",
            "Ibrahim",
            "Mahmoud",
            "Ali",
            "Rahman",
            "Aziz",
            "Al Sayed",
            "Abdel Rahman",
        ),
    ),
)

# --- Addresses -----------------------------------------------------------
# Fictional street/city combinations, deliberately not resolvable to real
# places. Long-form tokens are used as the "canonical" spelling so the
# address-variant channel can abbreviate them per address.py's tables.
STREET_NAMES: tuple[str, ...] = (
    "Maple",
    "Oak",
    "Cedar",
    "Elm",
    "Birch",
    "Willow",
    "Riverside",
    "Lakeview",
    "Sunset",
    "Hillcrest",
    "Meadow",
    "Franklin",
    "Jefferson",
    "Union",
    "Grant",
)
STREET_SUFFIX_LONG: tuple[str, ...] = (
    "Street",
    "Avenue",
    "Boulevard",
    "Drive",
    "Road",
    "Lane",
    "Court",
    "Place",
    "Terrace",
)
DIRECTIONAL_LONG: tuple[str, ...] = ("North", "South", "East", "West", "")
UNIT_LONG: tuple[str, ...] = ("Apartment", "Suite", "Unit", "")
CITIES: tuple[tuple[str, str, str], ...] = (
    # (city, state, zip prefix) — fictional pairings.
    ("Rivertown", "OH", "43"),
    ("Lakeside", "MI", "48"),
    ("Millbrook", "PA", "17"),
    ("Fairview", "TX", "75"),
    ("Cedar Falls", "IA", "50"),
    ("Brookhaven", "GA", "30"),
    ("Ashford", "CT", "06"),
    ("Pinecrest", "OR", "97"),
)
