import re
from datetime import date, datetime

from bs4 import BeautifulSoup, Tag
from bs4.element import AttributeValueList

from application.dto.document import LegalDocumentDTO
from src.application.dto.chunk import LegalChunkDTO
from src.application.parsers.enums import LegalDocumentDataType, LegalArticleDataType


class LegalDocumentParser:

    def __init__(self, html: str, base_url: str, document_id: str) -> None:
        self._soup = BeautifulSoup(html,"html.parser")

        self._document_id: str = document_id
        self._title: str
        self._last_modified: date
        self._base_url: str = base_url

        self._data_tree: str
        self._anchor: str | AttributeValueList | None = None

        self._book: str | None = None
        self._section: str | None = None
        self._sub_section: str | None = None
        self._chapter: str | None = None
        self._paragraph: str | None = None
        self._sub_paragraph: str | None = None

        self._preamble: str | None = None
        self._article: str | None = None
        self._item: str | None = None
        self._part: str | None = None
        self._sub_item: str | None = None
        self._references: list[str] = []

        self._chunks: list[LegalChunkDTO] = []

    def parse(self):
        self._set_title()
        self._set_last_modified()

        for p in self._soup.find_all("p"):
            self._set_anchor_and_data_tree(p)

            if not self._data_tree:
                continue

            text = self._get_text(p)

            # Book
            if self._data_tree.startswith(LegalDocumentDataType.BOOK):
                self._book = text

                self._section = None
                self._sub_section = None
                self._chapter = None
                self._paragraph = None
                self._sub_paragraph = None
                self._article = None
                self._item = None
                continue

            # Section
            if self._data_tree.startswith(LegalDocumentDataType.SECTION):
                self._section = text

                self._sub_section = None
                self._chapter = None
                self._paragraph = None
                self._sub_paragraph = None
                self._article = None
                self._item = None
                continue

            # Subsection
            if self._data_tree.startswith(LegalDocumentDataType.SUB_SECTION):
                if not text.startswith("{"):
                    self._sub_section = text

                    self._chapter = None
                    self._paragraph = None
                    self._sub_paragraph = None

                self._article = None
                self._item = None

                continue

            # Chapter
            if self._data_tree.startswith(LegalDocumentDataType.CHAPTER):
                self._chapter = text

                self._paragraph = None
                self._sub_paragraph = None
                self._article = None
                self._item = None
                continue

            # Paragraph, Subparagraph
            if self._data_tree.startswith(LegalDocumentDataType.PARAGRAPH):

                if "§" in text:
                    self._paragraph = text
                    self._sub_paragraph = None
                else:
                    self._sub_paragraph = text

                self._article = None
                self._item = None

                continue

            # Preamble
            if self._data_tree.startswith(LegalArticleDataType.PREAMBLE):
                self._preamble = text
                self._part = None

                if self._preamble and self._preamble.endswith((".", ";", "!", "?")):
                    self._flush()

                continue

            # Article
            if self._data_tree.startswith(LegalArticleDataType.ARTICLE):
                self._article = text
                self._item = None
                self._part = None

                if self._article and self._article.endswith((".", ";", "!", "?")):
                    self._flush()

                continue

            # Item
            if self._data_tree.startswith(LegalArticleDataType.ITEM):
                self._item = text
                self._sub_item = None

                if self._part and self._part.endswith((".", ";", "!", "?")):
                    self._part = None

                if self._item and self._item.endswith((".", ";", "!", "?")):
                    self._flush()

                continue

            # Sub item
            if self._data_tree.startswith(LegalArticleDataType.SUB_ITEM):
                self._sub_item = text

                if self._sub_item and self._sub_item.endswith((".", ";", "!", "?")):
                    self._flush()

                    if self._sub_item.endswith("."):
                        self._part = None

                    self._sub_item = None

                continue

            # Part
            if self._data_tree.startswith(LegalArticleDataType.PART):

                if text and text.endswith((".", ";", "!", "?")):
                    if self._part:
                        self._part += f"\n{text}"
                        self._flush()
                        self._part = None
                    else:
                        self._part = text
                        self._flush()
                        self._part = None

                if text and text.endswith((" ", ",", ":")):
                    if self._part:
                        self._part += f"\n{text}"
                    else:
                        self._part = text

                continue

        return LegalDocumentDTO(
            external_id=self._document_id,
            title='',
            source_url=f"{self._base_url}{self._document_id}",
            last_modified=self._last_modified,
            chunks=self._chunks
        )

    def _flush(self) -> None:
        self._chunks.append(
            LegalChunkDTO(
                document_id=self._document_id,
                book=self._book,
                section=self._section,
                subsection=self._sub_section,
                chapter=self._chapter,
                paragraph=self._paragraph,
                sub_paragraph=self._sub_paragraph,
                article=self._article,
                part=self._part,
                item=self._item,
                sub_item=self._sub_item,
                data_tree=self._data_tree,
                text=self._build_text(),
                source_url=f"{self._base_url}{self._document_id}#{self._anchor or ''}",
                references=list(self._references),
            )
        )

    def _build_text(self) -> str:
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

        if self._part and self._part.endswith(':'):
            parts.append(self._part)
            parts.append(self._item)
            parts.append(self._sub_item)
        else:
            parts.append(self._item)
            parts.append(self._sub_item)
            parts.append(self._part)

        return "\n".join(
            value for value in parts if value
        )

    def _matches(self, pattern: re.Pattern[str]) -> bool:
        if not self._data_tree:
            return False

        return bool(pattern.fullmatch(self._data_tree))

    @staticmethod
    def _get_text(tag: Tag) -> str:
        return " ".join(tag.get_text(separator=" ", strip=True).split())

    def _set_anchor_and_data_tree(self, tag: Tag) -> None:
        self._data_tree = None
        self._anchor = None

        a = tag.find( name="a", attrs={"data-tree": True})

        if isinstance(a, Tag):
            self._data_tree = a.get("data-tree")
            self._anchor = a.get("name")

    def _set_last_modified(self):
        option = self._soup.find(name="option", attrs={"selected": True})

        if not isinstance(option, Tag):
            raise ValueError("Document modified date tag not found")

        text = option.get_text(" ", strip=True)
        match = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", text)

        if not match:
            raise ValueError("Document modified date not found")

        self._last_modified = datetime.strptime(match.group(1),"%d.%m.%Y").date()


    def _set_title(self) -> None:
        element = self._soup.find(name="span", attrs={"class": "txt-NZ"})

        if not isinstance(element, Tag):
            return

        self._title = element.get_text(strip=True)

