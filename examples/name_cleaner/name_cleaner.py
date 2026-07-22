"""Small functions for cleaning names typed by a person."""


def clean_name(name: str) -> str:
    """Lowercase words and put exactly one space between them."""
    words = name.split()
    cleaned_words = []

    for word in words:
        cleaned_words.append(word.lower())

    return " ".join(cleaned_words)


def clean_names(names: list[str]) -> list[str]:
    """Clean several names without changing their order."""
    cleaned_names = []

    for name in names:
        cleaned_names.append(clean_name(name))

    return cleaned_names
