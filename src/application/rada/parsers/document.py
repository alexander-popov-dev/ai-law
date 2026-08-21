import re
from datetime import date, datetime

from bs4 import BeautifulSoup, Tag
from bs4.element import AttributeValueList

from application.rada.dto.document import LegalDocumentDTO
from application.rada.dto.chunk import LegalChunkDTO
from application.rada.parsers.enums import (
    LegalDocumentDataType,
    LegalArticleDataType,
)


class RadaLegalDocumentParser:
    """
    Parses a legal document from the HTML structure used by
    zakon.rada.gov.ua.

    The parser walks through <p> elements and builds the current
    document hierarchy:

        Book
            Section
                Subsection
                    Chapter
                        Paragraph
                            Subparagraph
                                Article
                                    Part
                                    Item
                                    Sub item
                                    Sub sub item

    A LegalChunkDTO is created whenever the current logical block
    is considered complete according to the punctuation rules used
    by the source document.

    The parser also extracts document-level metadata:
        - document title
        - last modification date
    """

    def __init__(
        self,
        html: str,
        base_url: str,
        document_id: str,
    ) -> None:
        """Initialize the parser with document HTML and source metadata."""

        self._soup = BeautifulSoup(
            html,
            "html.parser",
        )

        self._document_id: str = document_id
        self._title: str
        self._last_modified: date
        self._base_url: str = base_url

        # Current HTML element metadata.
        #
        # data-tree identifies the type/position of the element
        # in the source document.
        self._data_tree: str
        self._anchor: str | AttributeValueList | None = None

        # --------------------------------------------------------------
        # Document hierarchy
        # --------------------------------------------------------------

        self._book: str | None = None
        self._section: str | None = None
        self._sub_section: str | None = None
        self._chapter: str | None = None
        self._paragraph: str | None = None
        self._sub_paragraph: str | None = None

        # --------------------------------------------------------------
        # Article hierarchy
        # --------------------------------------------------------------

        self._preamble: str | None = None
        self._article: str | None = None
        self._item: str | None = None
        self._sub_item: str | None = None
        self._sub_sub_item: str | None = None
        self._part: str | None = None

        # References extracted from the current element.
        self._references: list[str] = []

        # Completed chunks.
        self._chunks: list[LegalChunkDTO] = []
        self._skipped: list[str] = []

    def parse(self):
        """
        Parse the document and return a LegalDocumentDTO.

        Document metadata is extracted first. Then all <p> elements
        are processed sequentially because their order represents
        the structure of the legal document.
        """

        self._set_title()
        self._set_last_modified()

        for p in self._soup.find_all("p"):
            # Extract data-tree and anchor from the current <p>.
            self._set_anchor_and_data_tree(p)

            # Some <p> elements do not represent a meaningful
            # structural element and therefore do not have data-tree.
            if not self._data_tree:
                continue

            text = self._get_text(p)

            if self._data_tree == 'pp_1:pu1:st23':
                pass
            # ==========================================================
            # BOOK
            # ==========================================================

            if self._data_tree.startswith(LegalDocumentDataType.BOOK):
                self._book = text

                # A new book invalidates all lower-level hierarchy.
                self._section = None
                self._sub_section = None
                self._chapter = None
                self._paragraph = None
                self._sub_paragraph = None
                self._article = None
                self._item = None

                continue

            # ==========================================================
            # SECTION
            # ==========================================================

            if self._data_tree.startswith(LegalDocumentDataType.SECTION):
                self._section = text

                # A new section resets everything below it.
                self._sub_section = None
                self._chapter = None
                self._paragraph = None
                self._sub_paragraph = None
                self._article = None
                self._item = None

                continue

            # ==========================================================
            # SUBSECTION
            # ==========================================================

            if self._data_tree.startswith(LegalDocumentDataType.SUB_SECTION):
                # Some elements with the same data-tree prefix contain
                # service information instead of an actual subsection.
                if not text.startswith("{"):
                    self._sub_section = text

                    self._chapter = None
                    self._paragraph = None
                    self._sub_paragraph = None

                self._article = None
                self._item = None

                continue

            # ==========================================================
            # CHAPTER
            # ==========================================================

            if self._data_tree.startswith(LegalDocumentDataType.CHAPTER):
                self._chapter = text

                # A new chapter resets everything below it.
                self._paragraph = None
                self._sub_paragraph = None
                self._article = None
                self._item = None

                continue

            # ==========================================================
            # PARAGRAPH / SUBPARAGRAPH
            # ==========================================================

            if self._data_tree.startswith(LegalDocumentDataType.PARAGRAPH):

                # The source uses the same "fr" prefix for both
                # paragraphs and subparagraphs.
                #
                # The actual distinction is made by the text:
                #
                #     § 1. Господарські товариства
                #
                # is a paragraph, while:
                #
                #     1. Загальні положення
                #
                # is a subparagraph.
                if "§" in text:
                    self._paragraph = text
                    self._sub_paragraph = None
                else:
                    self._sub_paragraph = text

                # A new paragraph/subparagraph starts a new article
                # context.
                self._article = None
                self._item = None

                continue

            # ==========================================================
            # PREAMBLE
            # ==========================================================

            if self._data_tree.startswith(LegalArticleDataType.PREAMBLE):
                self._preamble = text

                # Preamble is independent from the previously accumulated
                # part.
                self._part = None

                # A preamble ending with sentence punctuation is treated
                # as a complete chunk.
                if self._preamble and self._preamble.endswith(
                    (".", ";", "!", "?"),
                ):
                    self._flush()

                continue

            # ==========================================================
            # ARTICLE
            # ==========================================================

            if self._data_tree.startswith(LegalArticleDataType.ARTICLE):
                self._article = text

                # A new article starts a new item/part context.
                self._item = None
                self._sub_item = None
                self._part = None
                self._references.clear()

                # If the article itself is a complete sentence,
                # immediately create a chunk.
                if self._article and self._article.endswith(
                    (".", ";", "!", "?"),
                ):
                    self._flush()

                continue

            # ==========================================================
            # ITEM
            # ==========================================================

            if self._data_tree.startswith(LegalArticleDataType.ITEM):
                self._item = text
                self._sub_item = None

                # A completed part belongs to the previous item.
                # Therefore it must be cleared before processing
                # a new item.
                if self._part and self._part.endswith(
                    (".", ";", "!", "?"),
                ):
                    self._part = None

                # A complete item becomes a chunk immediately.
                if self._item and self._item.endswith(
                    (".", ";", "!", "?"),
                ):
                    self._flush()

                continue

            # ==========================================================
            # SUB ITEM
            # ==========================================================

            if re.match(fr"^{LegalArticleDataType.SUB_ITEM}[0-9]", self._data_tree):
                self._sub_item = text

                # A complete sub-item becomes a chunk.
                if self._sub_item and self._sub_item.endswith(
                    (".", ";", "!", "?"),
                ):
                    self._flush()

                    # A period means that the current part is also
                    # considered complete.
                    if self._sub_item.endswith("."):
                        self._part = None

                    # self._sub_item = None

                continue

            # ==========================================================
            # PART
            # ==========================================================

            if self._data_tree.startswith(LegalArticleDataType.PART):

                # A part ending with sentence punctuation is complete.
                #
                # If a previous part fragment exists, append the new
                # text to it before flushing.
                if text and text.endswith(
                    (".", ";", "!", "?"),
                ):
                    if self._part:
                        self._part += f"\n{text}"
                        self._flush()
                        self._part = None
                    else:
                        self._part = text
                        self._flush()
                        self._part = None

                # A part ending with an open punctuation mark means
                # that the text continues in the following element.
                #
                # Examples:
                #
                #     "... належало:"
                #     "... а саме,"
                #
                if text and text.endswith(
                    (" ", ",", ":"),
                ):
                    if self._part:
                        self._part += f"\n{text}"
                    else:
                        self._part = text

                continue

            if re.match(fr"^{LegalArticleDataType.SUB_ITEM}[a-z]", self._data_tree):
                self._sub_sub_item = text

                # A complete sub-item becomes a chunk.
                if self._sub_sub_item and self._sub_sub_item.endswith(
                        (".", ";", "!", "?"),
                ):
                    self._flush()
                    self._sub_sub_item = None

                continue

            if text and text.startswith('{'):
                a = p.find('a', attrs={'href': True})
                if a:
                    reference = a.get('href')
                    if reference and reference.startswith('#'):
                        reference = f"{self._base_url}{self._document_id}{reference}"

                    self._references.append(reference)

                    continue

            self._skipped.append(self._data_tree)

        return LegalDocumentDTO(
            external_id=self._document_id,
            title=self._title,
            source_url=f"{self._base_url}{self._document_id}",
            last_modified=self._last_modified,
            chunks=self._chunks,
            skipped=self._skipped,
        )

    def _flush(self) -> None:
        """
        Create and store a LegalChunkDTO from the current parser state.

        The actual chunk text is assembled separately in _build_text()
        from the current document and article hierarchy.
        """

        self._chunks.append(
            LegalChunkDTO(
                document_id=self._document_id,
                book=self._book,
                section=self._section,
                sub_section=self._sub_section,
                chapter=self._chapter,
                paragraph=self._paragraph,
                sub_paragraph=self._sub_paragraph,
                article=self._article,
                part=self._part,
                item=self._item,
                sub_item=self._sub_item,
                data_tree=self._data_tree,
                text=self._build_text(),
                references=list(self._references),
                source_url=(
                    f"{self._base_url}"
                    f"{self._document_id}"
                    f"#{self._anchor or ''}"
                ),
            )
        )

    def _build_text(self) -> str:
        """
        Build the text that will be used as the chunk content.

        The text contains both the document hierarchy and the actual
        legal provision. This makes the resulting chunk self-contained
        for further processing, including embeddings and semantic search.

        The order of part/item/sub-item is intentionally different
        depending on whether the part ends with ':'.

        For an open part:

            Part:
            Item
            Sub item

        For a normal part:

            Item
            Sub item
            Part
        """

        parts = [
            self._title,
            self._book,
            self._section,
            self._sub_section,
            self._chapter,
            self._paragraph,
            self._sub_paragraph,
            self._article,
        ]

        if self._data_tree == 'pp4:ch_1:st81':
            pass

        # If the part ends with ':', it acts as a parent context
        # for the following item/sub-item.
        if not self._sub_item and self._part and self._part.endswith(":"):
            parts.append(self._part)
            parts.append(self._item)
            parts.append(self._sub_item)
        elif self._part and self._part.endswith(":"):
            parts.append(self._part)
            parts.append(self._item)
            parts.append(self._sub_item)
        else:
            # Otherwise the item/sub-item comes before the part.
            parts.append(self._item)
            parts.append(self._sub_item)
            parts.append(self._part)

        parts.append(self._sub_sub_item)

        # Ignore empty hierarchy levels.
        return "\n".join(
            value
            for value in parts
            if value
        )

    @staticmethod
    def _get_text(tag: Tag) -> str:
        """
        Extract normalized text from an HTML tag.

        Multiple whitespace characters and line breaks are collapsed
        into a single space.
        """

        return " ".join(tag.get_text(separator=" ", strip=True).split())

    def _set_anchor_and_data_tree(self, tag: Tag) -> None:
        """
        Extract data-tree and anchor attributes from the current <p>.

        The source HTML stores these values inside an <a> element:

            <a name="n652" data-tree="fr1"></a>
        """

        self._data_tree = None
        self._anchor = None

        a = tag.find(name="a", attrs={"data-tree": True})

        if isinstance(a, Tag):
            self._data_tree = a.get("data-tree")
            self._anchor = a.get("name")

    def _set_last_modified(self) -> None:
        """
        Extract the document's last modification date.

        The date is stored in the selected <option> element, for example:

            <option selected>
                18.08.2026 ...
            </option>

        The date is converted to datetime.date.
        """

        option = self._soup.find(name="option", attrs={"selected": True})

        if not isinstance(option, Tag):
            raise ValueError(
                "Document modified date tag not found"
            )

        text = option.get_text(" ", strip=True)

        match = re.search(pattern=r"\b(\d{2}\.\d{2}\.\d{4})\b", string=text)

        if not match:
            raise ValueError("Document modified date not found")

        self._last_modified = datetime.strptime(match.group(1), "%d.%m.%Y").date()

    def _set_title(self) -> None:
        """
        Extract the legal document title.

        The title is taken from the unique element with class
        'txt-NZ' used by zakon.rada.gov.ua.
        """

        element = self._soup.find(name="span", attrs={"class": "txt-NZ"})

        if not isinstance(element, Tag):
            return

        self._title = element.get_text(strip=True)
