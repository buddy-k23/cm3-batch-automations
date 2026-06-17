"""Hardened streaming XML parser for untrusted batch files (ADR 0019, S19-3, #396).

The parser treats the input as a sequence of **repeated record elements**
(default tag ``record``, overridable via *record_tag*) and streams them one at
a time with :func:`lxml.etree.iterparse`, running each mapped field's
``xml_xpath`` selector against the record subtree and emitting one flat
DataFrame row before clearing the element (``elem.clear()`` plus trimming
preceding siblings) to keep memory O(1) per record. The output shape is
identical to the fixed-width / delimited / NDJSON parsers: one column per
mapped field plus a leading ``__source_row__`` carrying the 1-indexed physical
source line where the record element opened (``elem.sourceline``).

This is the XML counterpart to :class:`~src.parsers.json_parser.JsonParser`
(ADR 0018), and reuses its conventions verbatim where XML parallels JSON:

- A repeated-child XPath (``transactions/transaction``) collapses to an integer
  **count** column named ``<field>_count`` — the load-bearing input to
  :meth:`~src.validators.field_validator.FieldValidator.validate_xml_array_length`
  (the XML twin of JSON's ``[*]``).
- Scalar paths preserve XML's three states so the engine can tell them apart: a
  resolved element/attribute value is written verbatim; a *present-but-empty*
  element (exists, no text) is written as Python ``None``; an *absent* path (the
  element/attribute did not resolve) is written as :data:`pandas.NA`. This
  absent-vs-empty distinction is what
  :meth:`~src.validators.field_validator.FieldValidator.validate_nested_required`
  keys off — reused unchanged from ADR 0018.

Two places XML does **not** parallel JSON (ADR 0019 §3, §4):

- **Attributes vs element text.** The XPath itself carries the distinction —
  a trailing ``@name`` step (``account/@id``) reads the named attribute; any
  other path reads element text. No extra column, no extra flag.
- **Namespaces.** By default the parser **strips namespaces** so the BA writes
  plain local names (``PmtInf/Amt``, never ``{urn:…}Amt``). The opt-in
  *namespace_aware* mode (plus a *namespaces* prefix→URI map) honours prefixed
  XPaths for the rare same-local-name disambiguation case.

**Security (ADR 0019 §5 — the load-bearing decision).** Fintech batch files are
untrusted. The single hardened :data:`_SAFE_PARSER` disables entity resolution,
DTD loading, and network access **by construction**, and the parser **rejects
any document carrying a DOCTYPE/DTD** with a clear error — defending against XXE
(external-entity file read / SSRF), the billion-laughs / quadratic
entity-expansion DoS, and external-DTD retrieval. A file that *tries* to use
entities fails loudly rather than validating against a partially-stripped tree.
"""

from typing import Any, Dict, List, Optional

import pandas as pd
from lxml import etree

from .base_parser import BaseParser

# The security-locked parser, constructed once and reused for every parse.
# Per ADR 0019 §5: entity resolution, DTD loading, and network access are all
# disabled by construction. ``huge_tree=False`` keeps libxml2's built-in
# size/depth guards on. This is a strictly stronger posture than
# ``defusedxml``-on-stdlib and keeps a single auditable hardened code path.
# The hardening flags, kept as a single dict so the streaming ``iterparse``
# path and the one-shot ``etree.parse`` path apply an identical posture.
# ``iterparse`` accepts these flags directly (it does not take a ``parser=``
# argument), while ``XMLParser`` takes them as constructor kwargs.
_SAFE_FLAGS = dict(
    resolve_entities=False,  # do not expand entities (kills XXE + billion-laughs)
    no_network=True,         # never fetch external DTDs/entities (kills SSRF)
    dtd_validation=False,    # do not validate against any DTD
    load_dtd=False,          # do not load the internal/external DTD subset
    huge_tree=False,         # keep libxml2 size/depth guards on
)

_SAFE_PARSER = etree.XMLParser(**_SAFE_FLAGS)

# Default record element name when the mapping/source config does not name one.
# ISO 20022 and similar feeds override this (e.g. ``CdtTrfTxInf``).
DEFAULT_RECORD_TAG = "record"


class XmlParser(BaseParser):
    """Streaming, hardened XML parser driven by per-field XPath selectors."""

    def __init__(
        self,
        file_path: str,
        fields: List[Dict[str, Any]],
        record_tag: str = DEFAULT_RECORD_TAG,
        namespace_aware: bool = False,
        namespaces: Optional[Dict[str, str]] = None,
    ):
        """Initialize the XML parser.

        Args:
            file_path: Path to the ``.xml`` file.
            fields: The mapping's flat field list. Each entry is a dict
                carrying at least a ``name`` and an ``xml_xpath`` (the XPath
                selector emitted by
                :meth:`~src.config.template_converter.TemplateConverter.from_xml_template`).
                Fields without an ``xml_xpath`` are skipped — they cannot be
                located in an XML record.
            record_tag: Local name of the repeated record element to stream
                over (default ``"record"``). Comes from the mapping/source
                config for feeds whose record element is named differently
                (e.g. ISO 20022's ``"CdtTrfTxInf"``).
            namespace_aware: When ``True``, the parser does NOT strip
                namespaces and resolves prefixed XPaths against *namespaces*.
                Default ``False`` — namespaces are stripped so the BA writes
                plain local names (ADR 0019 §3).
            namespaces: Prefix→URI map used only when *namespace_aware* is
                ``True`` (e.g. ``{"pmt": "urn:pmt"}``).
        """
        super().__init__(file_path)
        self.fields = fields or []
        self.record_tag = record_tag or DEFAULT_RECORD_TAG
        self.namespace_aware = namespace_aware
        self.namespaces = namespaces or {}

        # Pre-compile each field's locator once. A trailing ``@name`` step marks
        # an attribute read; otherwise the path reads element text. A field is
        # an "array" field (collapses to a ``<field>_count`` integer column,
        # mirroring JSON's ``[*]``) when explicitly flagged via ``xml_array`` or
        # when it is an ``integer``-typed field pointed at a repeated child
        # element (see :meth:`_looks_like_array`).
        self._compiled: List[Dict[str, Any]] = []
        for field in self.fields:
            xpath = field.get("xml_xpath")
            if not xpath:
                continue
            name = field["name"]
            is_array = bool(field.get("xml_array")) or self._looks_like_array(field)
            self._compiled.append({
                "name": name,
                "column": f"{name}_count" if is_array else name,
                "is_array": is_array,
                "xpath": xpath.strip(),
            })

    @staticmethod
    def _looks_like_array(field: Dict[str, Any]) -> bool:
        """Return True when a field's data type marks it as a repeated child.

        The JSON parser keys array-ness off a ``[*]`` suffix in the locator.
        XPath has no such suffix, so cardinality is declared by the field's
        ``data_type`` being ``integer`` *and* an explicit ``xml_array`` flag
        is honoured first. As a pragmatic default we treat an ``integer``
        typed field whose XPath has no attribute step as a count column — the
        BA points an integer field at the repeated child element (mirroring
        the JSON sample where ``transactions`` is the repeated child).

        Args:
            field: A single mapping field dict.

        Returns:
            ``True`` when the field should collapse to a ``<field>_count``
            integer column.
        """
        xpath = (field.get("xml_xpath") or "").strip()
        # An attribute read is never an array.
        if xpath.split("/")[-1].startswith("@"):
            return False
        data_type = str(field.get("data_type", "")).strip().lower()
        return data_type in ("integer", "int")

    def parse(self) -> pd.DataFrame:
        """Stream-parse the XML file into a flat DataFrame.

        Returns:
            DataFrame with one column per mapped field (array fields become a
            ``<field>_count`` integer column), plus a leading ``__source_row__``
            column holding the 1-indexed physical source line of each record
            element. Scalar values are written verbatim; a present-but-empty
            element resolves to ``None``; an absent path resolves to
            :data:`pandas.NA`.

        Raises:
            ValueError: If the document carries a DOCTYPE/DTD (rejected
                fail-closed — XXE / billion-laughs defence), or if the XML is
                malformed. The DOCTYPE error never echoes document content so a
                malicious external entity cannot exfiltrate via the error path.
        """
        self._reject_doctype()

        columns = ["__source_row__"] + [c["column"] for c in self._compiled]
        source_rows: List[int] = []
        rows: List[Dict[str, Any]] = []

        try:
            # No ``tag=`` filter: namespace-strip-by-default means the record
            # element may be namespace-qualified, so we match on its *local
            # name* per end-event rather than on a fully-qualified tag.
            context = etree.iterparse(
                self.file_path,
                events=("end",),
                **_SAFE_FLAGS,
            )
            for _event, elem in context:
                if self._local_name(elem.tag) != self.record_tag:
                    continue
                source_rows.append(elem.sourceline or 0)
                rows.append(self._resolve_record(elem))
                # O(1) memory: clear the processed element and trim preceding
                # siblings so libxml2 can free them.
                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
        except etree.XMLSyntaxError as exc:
            raise ValueError(f"Malformed XML: {exc}") from exc
        except ValueError:
            raise
        except Exception as exc:  # pragma: no cover - I/O / unexpected libxml2
            raise ValueError(f"Failed to parse XML file: {exc}") from exc

        if not rows:
            return pd.DataFrame(columns=columns)

        df = pd.DataFrame(rows, columns=[c["column"] for c in self._compiled])
        df.insert(0, "__source_row__", source_rows)
        return df

    def _reject_doctype(self) -> None:
        """Reject any document declaring a DOCTYPE/DTD (ADR 0019 §5).

        Reads only the leading bytes of the file looking for a ``<!DOCTYPE``
        token before the root element. A DOCTYPE is the entry point for XXE,
        billion-laughs, and external-DTD attacks, so the parser fails closed.
        The error message never includes document content.

        Raises:
            ValueError: When a ``<!DOCTYPE`` declaration is present.
        """
        try:
            with open(self.file_path, "r", encoding="utf-8", errors="ignore") as fh:
                head = fh.read(8192)
        except OSError as exc:  # pragma: no cover - I/O errors
            raise ValueError(f"Failed to read XML file: {exc}") from exc
        # Scan only up to the root element start to avoid matching a literal
        # "<!DOCTYPE" inside element text/CDATA later in the document.
        prologue = head.split("<", 1)
        scan = head
        # The first significant "<" that is not "<?" (declaration) or "<!"
        # (DOCTYPE/comment) opens the root element; everything before it is the
        # prologue where a DOCTYPE would legally appear.
        if "<!DOCTYPE" in scan.upper():
            # Confirm it appears in the prologue (before the root element open).
            upper = scan.upper()
            doctype_pos = upper.index("<!DOCTYPE")
            # Find the first root-element open: a "<" not followed by "?" or "!".
            root_pos = self._find_root_open(scan)
            if root_pos == -1 or doctype_pos < root_pos:
                raise ValueError(
                    "XML document declares a DOCTYPE/DTD, which is rejected for "
                    "security reasons (XXE / entity-expansion defence per ADR "
                    "0019). Remove the DOCTYPE and any entity definitions."
                )
        del prologue

    @staticmethod
    def _find_root_open(text: str) -> int:
        """Return the index of the first root-element ``<`` (not ``<?`` / ``<!``).

        Args:
            text: Leading document text.

        Returns:
            Index of the first element-opening ``<``, or ``-1`` if none found
            in the scanned window.
        """
        i = 0
        n = len(text)
        while i < n:
            lt = text.find("<", i)
            if lt == -1:
                return -1
            nxt = text[lt + 1: lt + 2]
            if nxt not in ("?", "!"):
                return lt
            i = lt + 1
        return -1

    def _resolve_record(self, elem: "etree._Element") -> Dict[str, Any]:
        """Resolve every compiled XPath against one record element subtree.

        Args:
            elem: The record element produced by ``iterparse``.

        Returns:
            A flat dict keyed by output column name. Array fields yield an
            integer match count; scalar fields yield the resolved value,
            ``None`` for a present-but-empty element, or :data:`pandas.NA`
            when the path is absent.
        """
        resolved: Dict[str, Any] = {}
        for spec in self._compiled:
            value = self._resolve_one(elem, spec["xpath"], spec["is_array"])
            resolved[spec["column"]] = value
        return resolved

    def _resolve_one(self, elem: "etree._Element", xpath: str, is_array: bool) -> Any:
        """Resolve a single XPath against the record element.

        Honours the attribute-vs-element-text distinction (a trailing ``@name``
        step reads the attribute) and namespace mode (strip-by-default vs the
        opt-in prefixed-XPath resolution).

        Args:
            elem: Record element.
            xpath: The field's XPath locator.
            is_array: When ``True``, return the count of matched elements
                instead of a scalar value.

        Returns:
            For array fields: an ``int`` match count. For scalar fields: the
            attribute/element-text value, ``None`` for a present-empty element,
            or :data:`pandas.NA` when nothing matched.
        """
        steps = xpath.split("/")
        last = steps[-1]
        is_attr = last.startswith("@")

        if is_attr:
            element_path = "/".join(steps[:-1]) or "."
            attr_name = last[1:]
            matches = self._find(elem, element_path)
            if not matches:
                return pd.NA
            val = matches[0].get(attr_name)
            return val if val is not None else pd.NA

        matches = self._find(elem, xpath)
        if is_array:
            return len(matches)
        if not matches:
            return pd.NA
        # Present element: text or, for a present-but-empty element, None.
        text = matches[0].text
        return text if text is not None else None

    def _find(self, elem: "etree._Element", path: str) -> List["etree._Element"]:
        """Run an XPath against the record element, honouring namespace mode.

        In strip mode the tree has already been normalised to local names by
        :meth:`_strip_namespaces`, so a plain-local-name XPath matches directly.
        In namespace-aware mode the registered *namespaces* map is passed to
        ``lxml``'s XPath engine.

        Args:
            elem: Record element to search.
            path: XPath expression (already attribute-stripped by the caller
                when resolving an attribute).

        Returns:
            List of matched elements (possibly empty).
        """
        if path in (".", ""):
            return [elem]
        if self.namespace_aware:
            return elem.xpath(path, namespaces=self.namespaces)
        # Strip mode: tags carry namespaces in Clark notation, so a plain
        # local-name XPath would not match. Use a namespace-agnostic XPath by
        # matching on local-name() at each step.
        return elem.xpath(self._local_name_xpath(path))

    @staticmethod
    def _local_name_xpath(path: str) -> str:
        """Rewrite a plain-local-name path into a namespace-agnostic XPath.

        Each step ``foo`` becomes ``*[local-name()='foo']`` so the selector
        matches regardless of any namespace on the element — implementing
        strip-by-default without mutating the parsed tree.

        Args:
            path: A ``/``-separated local-name path (e.g. ``customer/id``).

        Returns:
            A namespace-agnostic XPath string.
        """
        parts = [p for p in path.split("/") if p not in ("", ".")]
        return "./" + "/".join(f"*[local-name()='{p}']" for p in parts)

    @staticmethod
    def _local_name(tag: Any) -> str:
        """Return the local name of an element tag, stripping any namespace.

        Args:
            tag: An lxml element tag (``str`` in Clark notation
                ``{uri}local`` when namespaced, or a callable for comments/PIs).

        Returns:
            The local name, or an empty string for non-element nodes
            (comments, processing instructions).
        """
        if not isinstance(tag, str):
            return ""
        if tag.startswith("{"):
            return tag.split("}", 1)[1]
        return tag

    def validate_format(self) -> bool:
        """Validate that the file is well-formed XML with an element root.

        Returns:
            ``True`` when the file parses with the hardened parser and exposes
            a root element. Returns ``False`` for unreadable, non-XML, or
            DOCTYPE-bearing content (the latter is rejected by construction).
        """
        try:
            self._reject_doctype()
            tree = etree.parse(self.file_path, parser=_SAFE_PARSER)
            return tree.getroot() is not None
        except Exception:
            return False
