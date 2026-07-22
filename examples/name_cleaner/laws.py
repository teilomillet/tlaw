import tlaw

from name_cleaner import clean_name, clean_names


@tlaw.law
def cleaning_a_name_follows_the_written_steps(name: str) -> bool:
    expected_words = []

    for word in name.split():
        expected_words.append(word.lower())

    expected_name = " ".join(expected_words)
    return clean_name(name) == expected_name


@tlaw.law
def cleaning_twice_is_the_same_as_cleaning_once(name: str) -> bool:
    return clean_name(clean_name(name)) == clean_name(name)


@tlaw.law
def spaces_around_a_name_do_not_change_the_result(name: str) -> bool:
    return clean_name("  " + name + "  ") == clean_name(name)


@tlaw.law
def cleaning_many_names_matches_cleaning_each_name(names: list[str]) -> bool:
    expected_names = []

    for name in names:
        expected_names.append(clean_name(name))

    return clean_names(names) == expected_names
