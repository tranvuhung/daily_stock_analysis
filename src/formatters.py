# -*- coding: utf-8 -*-
"""
===================================
Formatting utilities module
===================================

Provides content formatting helpers that convert generic formats into platform-specific formats.
"""

import re
from typing import Callable, List, Optional

import markdown2

TRUNCATION_SUFFIX = "\n\n...(本段内容过长已截断)"
PAGE_MARKER_PREFIX = f"\n\n📄"
PAGE_MARKER_SAFE_BYTES = 16 # "\n\n📄 9999/9999"
PAGE_MARKER_SAFE_LEN = 13   # "\n\n📄 9999/9999"
MIN_MAX_WORDS = 10
MIN_MAX_BYTES = 40
FENCED_CODE_BLOCK_RE = re.compile(r"(^```[^\n]*\n.*?^```[ \t]*$)", re.MULTILINE | re.DOTALL)
FENCED_CODE_BLOCK_PLACEHOLDER = "@@DSA_FENCED_CODE_BLOCK_{}@@"
HIDDEN_MARKDOWN_METADATA_RE = re.compile(
    r"^\[dsa-[^\]]+\]:\s+#\s+\([^)\n]*\)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Unicode code point ranges for special characters.
_SPECIAL_CHAR_RANGE = (0x10000, 0xFFFFF)
_SPECIAL_CHAR_REGEX = re.compile(r'[\U00010000-\U000FFFFF]')


def _page_marker(i: int, total: int) -> str:
    return f"{PAGE_MARKER_PREFIX} {i+1}/{total}"


def _is_special_char(c: str) -> bool:
    """Check whether a character is a special character
    
    Args:
        c: Character
        
    Returns:
        True if the character is special, otherwise False
    """
    if len(c) != 1:
        return False
    cp = ord(c)
    return _SPECIAL_CHAR_RANGE[0] <= cp <= _SPECIAL_CHAR_RANGE[1]


def _count_special_chars(s: str) -> int:
    """
    Count the special characters in a string
    
    Args:
        s: String
    """
    # reg find all (0x10000, 0xFFFFF)
    match = _SPECIAL_CHAR_REGEX.findall(s)
    return len(match)


def _effective_len(s: str, special_char_len: int = 2) -> int:
    """
    Compute the effective length of a string
    
    Args:
        s: String
        special_char_len: Length of each special character, default 2
        
    Returns:
        Effective length of s
    """
    n = len(s)
    n += _count_special_chars(s) * (special_char_len - 1)
    return n


def _slice_at_effective_len(s: str, effective_len: int, special_char_len: int = 2) -> tuple[str, str]:
    """
    Split a string by effective length
    
    Args:
        s: String
        effective_len: Effective length
        special_char_len: Length of each special character, default 2
        
    Returns:
        The leading and trailing parts after splitting
    """
    if _effective_len(s, special_char_len) <= effective_len:
        return s, ""
    
    s_ = s[:effective_len]
    n_special_chars = _count_special_chars(s_)
    residual_lens = n_special_chars * (special_char_len - 1) + len(s_) - effective_len
    while residual_lens > 0:
        residual_lens -= special_char_len if _is_special_char(s_[-1]) else 1
        s_ = s_[:-1]
    return s_, s[len(s_):]


def markdown_to_html_document(markdown_text: str) -> str:
    """
    Convert Markdown to a complete HTML document (for email, md2img, etc.).

    Uses markdown2 with table and code block support, wraps with inline CSS
    for compact, readable layout. Reused by notification email and md2img.

    Args:
        markdown_text: Raw Markdown content.

    Returns:
        Full HTML document string with DOCTYPE, head, and body.
    """
    html_content = markdown2.markdown(
        markdown_text,
        extras=["tables", "fenced-code-blocks", "break-on-newline", "cuddled-lists"],
    )

    css_style = """
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                line-height: 1.5;
                color: #24292e;
                font-size: 14px;
                padding: 15px;
                max-width: 900px;
                margin: 0 auto;
            }
            h1 {
                font-size: 20px;
                border-bottom: 1px solid #eaecef;
                padding-bottom: 0.3em;
                margin-top: 1.2em;
                margin-bottom: 0.8em;
                color: #0366d6;
            }
            h2 {
                font-size: 18px;
                border-bottom: 1px solid #eaecef;
                padding-bottom: 0.3em;
                margin-top: 1.0em;
                margin-bottom: 0.6em;
            }
            h3 {
                font-size: 16px;
                margin-top: 0.8em;
                margin-bottom: 0.4em;
            }
            p {
                margin-top: 0;
                margin-bottom: 8px;
            }
            table {
                border-collapse: collapse;
                width: 100%;
                margin: 12px 0;
                display: block;
                overflow-x: auto;
                font-size: 13px;
            }
            th, td {
                border: 1px solid #dfe2e5;
                padding: 6px 10px;
                text-align: left;
            }
            th {
                background-color: #f6f8fa;
                font-weight: 600;
            }
            tr:nth-child(2n) {
                background-color: #f8f8f8;
            }
            tr:hover {
                background-color: #f1f8ff;
            }
            blockquote {
                color: #6a737d;
                border-left: 0.25em solid #dfe2e5;
                padding: 0 1em;
                margin: 0 0 10px 0;
            }
            code {
                padding: 0.2em 0.4em;
                margin: 0;
                font-size: 85%;
                background-color: rgba(27,31,35,0.05);
                border-radius: 3px;
                font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
            }
            pre {
                padding: 12px;
                overflow: auto;
                line-height: 1.45;
                background-color: #f6f8fa;
                border-radius: 3px;
                margin-bottom: 10px;
            }
            hr {
                height: 0.25em;
                padding: 0;
                margin: 16px 0;
                background-color: #e1e4e8;
                border: 0;
            }
            ul, ol {
                padding-left: 20px;
                margin-bottom: 10px;
            }
            li {
                margin: 2px 0;
            }
        """

    return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                {css_style}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """


def markdown_to_plain_text(markdown_text: str) -> str:
    """
    Convert Markdown to plain text
    
    Remove Markdown formatting marks while keeping it readable
    """
    text = markdown_text

    # Remove Markdown reference definitions (e.g. hidden metadata markers)
    text = strip_markdown_reference_definitions(text)
    
    # Remove heading marks # ## ###
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    
    # Remove bold **text** -> text
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    
    # Remove italic *text* -> text
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    
    # Remove blockquote > text -> text
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
    
    # Remove list markers - item -> item
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    
    # Remove horizontal rules ---
    text = re.sub(r'^---+$', '────────', text, flags=re.MULTILINE)
    
    # Remove table syntax |---|---|
    text = re.sub(r'\|[-:]+\|[-:|\s]+\|', '', text)
    text = re.sub(r'^\|(.+)\|$', r'\1', text, flags=re.MULTILINE)
    
    # Collapse extra blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


def strip_hidden_markdown_metadata(markdown_text: str) -> str:
    """Strip hidden internal Markdown metadata lines while preserving normal refs."""

    return HIDDEN_MARKDOWN_METADATA_RE.sub("", markdown_text)


def strip_markdown_reference_definitions(markdown_text: str) -> str:
    """Strip Markdown reference-definition lines while preserving the rest."""

    return re.sub(r'^\[[^\]]+\]:\s+.*$', '', markdown_text, flags=re.MULTILINE)


def _bytes(s: str) -> int:
    return len(s.encode('utf-8'))


def utf8_len(s: str) -> int:
    """Return the number of UTF-8 bytes used by ``s``."""

    return len(s.encode("utf-8"))


def utf16_len(s: str) -> int:
    """Return the number of UTF-16 code units used by ``s``.

    Telegram's 4096-character message limit is effectively counted in UTF-16
    units, so astral-plane characters such as emoji consume two units.
    """

    return len(s.encode("utf-16-le")) // 2


def _custom_unit_to_index(text: str, budget: int, len_fn: Callable[[str], int]) -> int:
    """Map a custom-unit budget to the largest safe Python string index."""

    if len_fn(text) <= budget:
        return len(text)
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len_fn(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _has_unclosed_inline_code(text: str) -> bool:
    """Return whether ``text`` ends inside a single-backtick inline code span."""

    escaped = False
    count = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and not escaped:
            escaped = True
            i += 1
            continue
        if ch == "`" and not escaped:
            # Triple backticks are handled as fenced code by the caller; skip
            # them here so fence delimiters do not look like inline spans.
            if text[i:i + 3] == "```":
                i += 3
                escaped = False
                continue
            count += 1
        escaped = False
        i += 1
    return count % 2 == 1


def _last_unclosed_markdown_link_start(text: str) -> int:
    """Return the start index of an inline Markdown link split in progress."""

    last_open_paren = text.rfind("](")
    last_close_paren = text.rfind(")")
    if last_open_paren > last_close_paren:
        label_start = text.rfind("[", 0, last_open_paren)
        return label_start if label_start >= 0 else last_open_paren

    last_open_bracket = text.rfind("[")
    last_close_bracket = text.rfind("]")
    if last_open_bracket > last_close_bracket:
        return last_open_bracket

    return -1


def chunk_markdown_preserving_blocks(
    content: str,
    max_units: int,
    *,
    len_fn: Optional[Callable[[str], int]] = None,
    add_page_marker: bool = False,
) -> List[str]:
    """Split Markdown while preserving common formatting boundaries.

    The splitter is intentionally conservative and does not alter report
    semantics.  If a split lands inside a fenced code block, the current chunk is
    closed and the next chunk reopens the same fence language.  It also avoids
    splitting inside inline code spans and Markdown links, and supports custom
    length functions such as :func:`utf16_len`.
    """

    measure = len_fn or len
    if max_units < MIN_MAX_WORDS:
        raise ValueError(f"max_units={max_units} < {MIN_MAX_WORDS}, 可能陷入无限递归。")
    if measure(content) <= max_units:
        return [content]

    marker_reserve = measure(_page_marker(9998, 9998)) if add_page_marker else 0
    indicator_reserve = measure("\n\n(9999/9999)")
    fence_close = "\n```"
    chunks: List[str] = []
    remaining = content
    carry_lang: Optional[str] = None

    while remaining:
        prefix = f"```{carry_lang}\n" if carry_lang is not None else ""
        headroom = max_units - marker_reserve - indicator_reserve - measure(prefix) - measure(fence_close)
        if headroom < MIN_MAX_WORDS:
            headroom = max(MIN_MAX_WORDS, max_units - marker_reserve - indicator_reserve - measure(prefix))
        if headroom <= 0:
            raise ValueError("max_units is too small for markdown-preserving chunking")

        if measure(prefix) + measure(remaining) <= max_units - marker_reserve - indicator_reserve:
            chunks.append(prefix + remaining)
            break

        cp_limit = (
            _custom_unit_to_index(remaining, headroom, measure)
            if measure is not len else min(headroom, len(remaining))
        )
        region = remaining[:cp_limit]
        split_at = region.rfind("\n\n")
        if split_at < cp_limit // 2:
            split_at = region.rfind("\n")
        if split_at < cp_limit // 2:
            split_at = region.rfind(" ")
        if split_at < 1:
            split_at = cp_limit

        candidate = remaining[:split_at]
        unsafe_start = len(candidate)
        if _has_unclosed_inline_code(candidate):
            last_tick = candidate.rfind("`")
            if last_tick >= 0:
                unsafe_start = min(unsafe_start, last_tick)

        link_start = _last_unclosed_markdown_link_start(candidate)
        if link_start >= 0:
            unsafe_start = min(unsafe_start, link_start)

        if unsafe_start < len(candidate):
            safe_split = max(candidate.rfind(" ", 0, unsafe_start), candidate.rfind("\n", 0, unsafe_start))
            if safe_split > 0:
                split_at = safe_split

        chunk_body = remaining[:split_at].rstrip()
        in_code = carry_lang is not None
        lang = carry_lang or ""
        for line in chunk_body.split("\n"):
            stripped = line.strip()
            if stripped.startswith("```"):
                if in_code:
                    in_code = False
                    lang = ""
                else:
                    in_code = True
                    tag = stripped[3:].strip()
                    lang = tag.split()[0] if tag else ""

        next_remaining = remaining[split_at:]
        if next_remaining.startswith("\n"):
            next_remaining = next_remaining[1:]
        elif not in_code and next_remaining.startswith(" "):
            line_start = remaining.rfind("\n", 0, split_at) + 1
            if remaining[line_start:split_at].strip(" \t"):
                next_remaining = next_remaining[1:]
        remaining = next_remaining
        full_chunk = prefix + chunk_body

        if in_code:
            full_chunk += fence_close
            carry_lang = lang
        else:
            carry_lang = None

        chunks.append(full_chunk)

    if len(chunks) > 1:
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            suffix = f"\n\n({i + 1}/{total})"
            if add_page_marker:
                suffix += _page_marker(i, total)
            chunks[i] = chunk + suffix
    elif add_page_marker:
        chunks[0] = chunks[0] + _page_marker(0, 1)
    return chunks


def _is_markdown_table_separator(row: str) -> bool:
    return bool(re.match(r'^\s*\|?\s*[:-]+\s*(\|\s*[:-]+\s*)+\|?\s*$', row))


def _parse_markdown_table_row(row: str) -> List[str]:
    stripped = row.strip()
    if stripped.startswith('|'):
        stripped = stripped[1:]
    if stripped.endswith('|'):
        stripped = stripped[:-1]
    return [c.strip() for c in stripped.split('|')]


def _strip_inline_markdown(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^\*\*(.+)\*\*$', r'\1', text)
    text = re.sub(r'^\*(.+)\*$', r'\1', text)
    text = text.replace("**", "")
    return text.strip()


def _format_two_column_table_row(header: List[str], row: List[str]) -> str:
    key = _strip_inline_markdown(row[0]) if row else ""
    value = _strip_inline_markdown(row[1]) if len(row) > 1 else ""
    value_header = _strip_inline_markdown(header[1]) if len(header) > 1 else ""

    if not value:
        return key
    if value.upper() == "N/A" and value_header in {"类型", "Type"}:
        return key
    return f"{key}：{value}"


def _flush_table_as_key_value_rows(buffer: List[str], output: List[str], *, bullet: str) -> None:
    if not buffer:
        return

    rows = []
    for raw in buffer:
        if _is_markdown_table_separator(raw):
            continue
        parsed = _parse_markdown_table_row(raw)
        if parsed:
            rows.append(parsed)

    if not rows:
        return

    header = rows[0]
    data_rows = rows[1:] if len(rows) > 1 else []
    for row in data_rows:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        if len(header) == 2 and len(row) >= 2:
            output.append(f"{bullet} {_format_two_column_table_row(header, row)}")
            continue

        pairs = []
        for idx, cell in enumerate(row):
            key = _strip_inline_markdown(header[idx]) if idx < len(header) else f"列{idx + 1}"
            pairs.append(f"{key}：{_strip_inline_markdown(cell)}")
        output.append(f"{bullet} {' | '.join(pairs)}")


def _protect_fenced_code_blocks(content: str) -> tuple[str, List[str]]:
    blocks: List[str] = []

    def _replace(match: re.Match) -> str:
        blocks.append(match.group(0))
        return FENCED_CODE_BLOCK_PLACEHOLDER.format(len(blocks) - 1)

    return FENCED_CODE_BLOCK_RE.sub(_replace, content), blocks


def _restore_fenced_code_blocks(content: str, blocks: List[str]) -> str:
    restored = content
    for idx, block in enumerate(blocks):
        restored = restored.replace(FENCED_CODE_BLOCK_PLACEHOLDER.format(idx), block)
    return restored


def _transform_outside_fenced_code_blocks(content: str, transform: Callable[[str], str]) -> str:
    protected, blocks = _protect_fenced_code_blocks(content)
    return _restore_fenced_code_blocks(transform(protected), blocks)


def _markdown_tables_to_key_value_rows_unprotected(content: str, *, bullet: str) -> str:
    """Convert pipe tables to compact key-value rows for chat clients."""

    lines: List[str] = []
    table_buffer: List[str] = []

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith('|'):
            table_buffer.append(line)
            continue

        if table_buffer:
            _flush_table_as_key_value_rows(table_buffer, lines, bullet=bullet)
            table_buffer = []

        lines.append(line)

    if table_buffer:
        _flush_table_as_key_value_rows(table_buffer, lines, bullet=bullet)

    return "\n".join(lines).strip()


def markdown_tables_to_key_value_rows(content: str, *, bullet: str = "•") -> str:
    """Convert pipe tables to compact key-value rows outside fenced code blocks."""

    return _transform_outside_fenced_code_blocks(
        content,
        lambda text: _markdown_tables_to_key_value_rows_unprotected(text, bullet=bullet),
    ).strip()


def _chunk_by_max_bytes(content: str, max_bytes: int) -> List[str]:
    if _bytes(content) <= max_bytes:
        return [content]
    if max_bytes < MIN_MAX_BYTES:
        raise ValueError(f"max_bytes={max_bytes} < {MIN_MAX_BYTES}, 可能陷入无限递归。")
    
    sections: List[str] = []
    suffix = TRUNCATION_SUFFIX
    effective_max_bytes = max_bytes - _bytes(suffix)
    if effective_max_bytes <= 0:
        effective_max_bytes = max_bytes
        suffix = ""
        
    while True:
        chunk, content = slice_at_max_bytes(content, effective_max_bytes)
        if content.strip() != "":
            sections.append(chunk + suffix)
        else:
            # Last segment: append it and exit the loop
            sections.append(chunk)
            break
    return sections


def chunk_content_by_max_bytes(content: str, max_bytes: int, add_page_marker: bool = False) -> List[str]:
    """
    Smartly split message content by byte count
    
    Args:
        content: Full message content
        max_bytes: Maximum bytes per message
        add_page_marker: Whether to add page markers
        
    Returns:
        List of split chunks
    """
    def _chunk(content: str, max_bytes: int) -> List[str]:
        # Prefer splitting on separators/headings so pages break naturally
        if max_bytes < MIN_MAX_BYTES:
            raise ValueError(f"max_bytes={max_bytes} < {MIN_MAX_BYTES}, 可能陷入无限递归。")
        
        if _bytes(content) <= max_bytes:
            return [content]
        
        sections, separator = _chunk_by_separators(content)
        if separator == "" and len(sections) == 1:
            # Cannot split smartly; force split by length
            return _chunk_by_max_bytes(content, max_bytes)
        
        chunks: List[str] = []
        current_chunk: List[str] = []
        current_bytes = 0
        separator_bytes = _bytes(separator) if separator else 0
        effective_max_bytes = max_bytes - separator_bytes

        for section in sections:
            section += separator
            section_bytes = _bytes(section)
            
            # A single section is already too long; force truncation
            if section_bytes > effective_max_bytes:
                # Save the content accumulated so far first
                if current_chunk:
                    chunks.append("".join(current_chunk))
                    current_chunk = []
                    current_bytes = 0

                # Force truncation by bytes so the whole section is not lost
                section_chunks = _chunk(
                    section[:-separator_bytes], effective_max_bytes
                )
                section_chunks[-1] = section_chunks[-1] + separator
                chunks.extend(section_chunks)
                continue

            # Check whether adding it would exceed the limit
            if current_bytes + section_bytes > effective_max_bytes:
                # Save the current chunk and start a new one
                if current_chunk:
                    chunks.append("".join(current_chunk))
                current_chunk = [section]
                current_bytes = section_bytes
            else:
                current_chunk.append(section)
                current_bytes += section_bytes
                
        # Append the final chunk
        if current_chunk:
            chunks.append("".join(current_chunk))
            
        # Remove the separator from the last chunk
        if (chunks and 
            len(chunks[-1]) > separator_bytes and 
            chunks[-1][-separator_bytes:] == separator
        ):
            chunks[-1] = chunks[-1][:-separator_bytes]
        
        return chunks
    
    if add_page_marker:
        max_bytes = max_bytes - PAGE_MARKER_SAFE_BYTES
    
    chunks = _chunk(content, max_bytes)
    if add_page_marker:
        total_chunks = len(chunks)
        for i, chunk in enumerate(chunks):
            chunks[i] = chunk + _page_marker(i, total_chunks)
    return chunks


def slice_at_max_bytes(text: str, max_bytes: int) -> tuple[str, str]:
    """
    Truncate a string by bytes without cutting through a multi-byte character

    Args:
        text: String to truncate
        max_bytes: Maximum bytes

    Returns:
        (truncated string, remaining untruncated content)
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, ""

    # Search backward from max bytes for a complete UTF-8 character boundary
    truncated = encoded[:max_bytes]
    while truncated and (truncated[-1] & 0xC0) == 0x80:
        truncated = truncated[:-1]

    truncated = truncated.decode('utf-8', errors='ignore')
    return truncated, text[len(truncated):]


def _format_feishu_markdown_unprotected(content: str) -> str:
    lines = []
    table_buffer: List[str] = []

    for raw_line in content.splitlines():
        line = raw_line.rstrip()

        # Handle table rows
        if line.strip().startswith('|'):
            table_buffer.append(line)
            continue

        # Flush the table buffer
        if table_buffer:
            _flush_table_as_key_value_rows(table_buffer, lines, bullet="•")
            table_buffer = []

        # Convert headings (# ## ### etc.)
        if re.match(r'^#{1,6}\s+', line):
            title = re.sub(r'^#{1,6}\s+', '', line).strip()
            line = f"**{title}**" if title else ""
        # Convert blockquotes
        elif line.startswith('> '):
            quote = line[2:].strip()
            line = quote
        # Convert horizontal rules
        elif line.strip() == '---':
            line = '────────'
        # Convert list items
        elif line.startswith('- '):
            line = f"• {line[2:].strip()}"

        lines.append(line)

    # Handle a trailing table
    if table_buffer:
        _flush_table_as_key_value_rows(table_buffer, lines, bullet="•")

    return "\n".join(lines).strip()


def format_feishu_markdown(content: str) -> str:
    """
    Convert generic Markdown into a Feishu lark_md-friendly format

    Conversion rules:
    - Feishu does not support Markdown headings (# / ## / ###); use bold instead
    - Blockquotes are replaced with a prefix
    - Horizontal rules are normalized to a thin line
    - Tables are converted into item lists

    Args:
        content: Original Markdown content

    Returns:
        Converted Feishu Markdown content

    Example:
        >>> markdown = "# Title\\n> Quote\\n| Col1 | Col2 |"
        >>> formatted = format_feishu_markdown(markdown)
        >>> print(formatted)
        **Title**
        💬 Quote
        • Col1：Val1 | Col2：Val2
    """
    def _flush_table_rows(buffer: List[str], output: List[str]) -> None:
        """Convert rows in the table buffer to Feishu format"""
        if not buffer:
            return

        def _parse_row(row: str) -> List[str]:
            """Parse a table row and extract its cells"""
            return _parse_markdown_table_row(row)

        rows = []
        for raw in buffer:
            # Skip separator rows (e.g. |---|---|)
            if re.match(r'^\s*\|?\s*[:-]+\s*(\|\s*[:-]+\s*)+\|?\s*$', raw):
                continue
            parsed = _parse_row(raw)
            if parsed:
                rows.append(parsed)

        if not rows:
            return

        header = rows[0]
        data_rows = rows[1:] if len(rows) > 1 else []
        for row in data_rows:
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            pairs = []
            for idx, cell in enumerate(row):
                key = header[idx] if idx < len(header) else f"列{idx + 1}"
                pairs.append(f"{key}：{cell}")
            output.append(f"• {' | '.join(pairs)}")

    lines = []
    table_buffer: List[str] = []

    for raw_line in content.splitlines():
        line = raw_line.rstrip()

        # Handle table rows
        if line.strip().startswith('|'):
            table_buffer.append(line)
            continue

        # Flush the table buffer
        if table_buffer:
            _flush_table_rows(table_buffer, lines)
            table_buffer = []

        # Convert headings (# ## ### etc.)
        if re.match(r'^#{1,6}\s+', line):
            title = re.sub(r'^#{1,6}\s+', '', line).strip()
            line = f"**{title}**" if title else ""
        # Convert blockquotes
        elif line.startswith('> '):
            quote = line[2:].strip()
            line = f"💬 {quote}" if quote else ""
        # Convert horizontal rules
        elif line.strip() == '---':
            line = '────────'
        # Convert list items
        elif line.startswith('- '):
            line = f"• {line[2:].strip()}"

        lines.append(line)

    # Handle a trailing table
    if table_buffer:
        _flush_table_rows(table_buffer, lines)

    return "\n".join(lines).strip()


def _format_telegram_markdown_unprotected(content: str) -> str:
    """Convert common report Markdown to Telegram legacy Markdown."""

    result = _markdown_tables_to_key_value_rows_unprotected(content, bullet="-")
    result = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', result, flags=re.MULTILINE)
    result = re.sub(r'\*\*(.+?)\*\*', r'*\1*', result)
    result = re.sub(r'^\s*---+\s*$', '────────', result, flags=re.MULTILINE)
    result = _escape_telegram_non_link_markdown_chars(result)
    return result.strip()


def _escape_telegram_non_link_markdown_chars(content: str) -> str:
    """Escape Telegram Markdown link metacharacters outside valid links."""

    links: list[str] = []

    def _save_link(match: re.Match) -> str:
        links.append(match.group(0))
        return f"@@DSA_TELEGRAM_LINK_{len(links) - 1}@@"

    result = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _save_link, content)
    for char in ("[", "]", "(", ")"):
        result = result.replace(char, f"\\{char}")

    for index, link in enumerate(links):
        result = result.replace(f"@@DSA_TELEGRAM_LINK_{index}@@", link)
    return result


def format_telegram_markdown(content: str) -> str:
    """Convert common report Markdown to Telegram legacy Markdown."""

    return _transform_outside_fenced_code_blocks(
        content,
        _format_telegram_markdown_unprotected,
    ).strip()


def format_wechat_markdown(content: str) -> str:
    """Keep WeChat Markdown style while making pipe tables mobile-readable."""

    result = markdown_tables_to_key_value_rows(content, bullet="•")
    result = re.sub(r'^\s*---+\s*$', '────────', result, flags=re.MULTILINE)
    return result.strip()


def _format_slack_mrkdwn_unprotected(content: str) -> str:
    """Convert common report Markdown to Slack mrkdwn."""

    result = _markdown_tables_to_key_value_rows_unprotected(content, bullet="•")
    result = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<\2|\1>', result)
    result = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', result)
    result = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', result, flags=re.MULTILINE)
    result = re.sub(r'\*\*(.+?)\*\*', r'*\1*', result)
    result = re.sub(r'^\s*---+\s*$', '────────', result, flags=re.MULTILINE)
    return result.strip()


def format_slack_mrkdwn(content: str) -> str:
    """Convert common report Markdown to Slack mrkdwn."""

    return _transform_outside_fenced_code_blocks(
        content,
        _format_slack_mrkdwn_unprotected,
    ).strip()


def _chunk_by_separators(content: str) -> tuple[list[str], str]:
    """
    Split message content into chunks using separators and other special markers
    
    Args:
        content: Full message content
        
    Returns:
        sections: List of split chunks
        separator: Separator between chunks; None means splitting was not possible
    """
    # Smart split: prefer "---" (the separator between stocks)
    # then try splitting on each heading level
    if "\n---\n" in content:
        sections = content.split("\n---\n")
        separator = "\n---\n"
    elif "\n# " in content:
        # Split on # (supports level-1 headings)
        parts = content.split("\n# ")
        sections = [parts[0]] + [f"# {p}" for p in parts[1:]]
        separator = "\n"
    elif "\n## " in content:
        # Split on ## (supports level-2 headings)
        parts = content.split("\n## ")
        sections = [parts[0]] + [f"## {p}" for p in parts[1:]]
        separator = "\n"
    elif "\n### " in content:
        # Split on ###
        parts = content.split("\n### ")
        sections = [parts[0]] + [f"### {p}" for p in parts[1:]]
        separator = "\n"
    elif "\n**" in content:
        # Split on ** bold headings (for when the AI does not output standard Markdown headings)
        parts = content.split("\n**")
        sections = [parts[0]] + [f"**{p}" for p in parts[1:]]
        separator = "\n"
    elif "\n" in content:
        # Split on \n
        sections = content.split("\n")
        separator = "\n"
    else:
        return [content], ""
    return sections, separator


def _chunk_by_max_words(content: str, max_words: int, special_char_len: int = 2) -> list[str]:
    """
    Split message content by word count
    
    Args:
        content: Full message content
        max_words: Maximum words per message
        special_char_len: Length of each special character, default 2
        
    Returns:
        List of split chunks
    """
    if _effective_len(content, special_char_len) <= max_words:
        return [content]
    if max_words < MIN_MAX_WORDS:
        raise ValueError(
            f"max_words={max_words} < {MIN_MAX_WORDS}, 可能陷入无限递归。"
        )

    sections = []
    suffix = TRUNCATION_SUFFIX
    effective_max_words = max_words - len(suffix)  # Reserve room for the suffix to avoid overflow at the boundary
    if effective_max_words <= 0:
        effective_max_words = max_words
        suffix = ""

    while True:
        chunk, content = _slice_at_effective_len(content, effective_max_words, special_char_len)
        if content.strip() != "":
            sections.append(chunk + suffix)
        else:
            # Last segment: append it and exit the loop
            sections.append(chunk)
            break
    return sections


def chunk_content_by_max_words(
    content: str, 
    max_words: int, 
    special_char_len: int = 2,
    add_page_marker: bool = False
    ) -> list[str]:
    """
    Smartly split message content by word count
    
    Args:
        content: Full message content
        max_words: Maximum words per message
        special_char_len: Length of each special character, default 2
        add_page_marker: Whether to add page markers
        
    Returns:
        List of split chunks
    """
    def _chunk(content: str, max_words: int, special_char_len: int = 2) -> list[str]:
        if max_words < MIN_MAX_WORDS:
            # Safe guard against infinite recursion
            # In theory max_words could shrink without bound across recursions, but in practice this is unlikely,
            # unless _chunk_by_separators always returns a separator and the initial max_words is too small.
            raise ValueError(f"max_words={max_words} < {MIN_MAX_WORDS}, 可能陷入无限递归。")
        
        if _effective_len(content, special_char_len) <= max_words:
            return [content]

        sections, separator = _chunk_by_separators(content)
        if separator == "" and len(sections) == 1:
            # Cannot split smartly; force split by word count
            return _chunk_by_max_words(content, max_words, special_char_len)

        chunks = []
        current_chunk = []
        current_word_len = 0
        separator_len = len(separator) if separator else 0
        effective_max_words = max_words - separator_len # Reserve room for the separator to avoid overflow at the boundary

        for section in sections:
            section += separator
            section_word_len = _effective_len(section, special_char_len)

            # A single section is already too long; force truncation
            if section_word_len > max_words:
                # Save the content accumulated so far first
                if current_chunk:
                    chunks.append("".join(current_chunk))
                    current_chunk = []
                    current_word_len = 0

                # Force-truncate this oversized section
                section_chunks = _chunk(
                    section[:-separator_len], effective_max_words, special_char_len
                    )
                section_chunks[-1] = section_chunks[-1] + separator
                chunks.extend(section_chunks)
                continue

            # Check whether adding it would exceed the limit
            if current_word_len + section_word_len > max_words:
                # Save the current chunk and start a new one
                if current_chunk:
                    chunks.append("".join(current_chunk))
                current_chunk = [section]
                current_word_len = section_word_len
            else:
                current_chunk.append(section)
                current_word_len += section_word_len

        # Append the final chunk
        if current_chunk:
            chunks.append("".join(current_chunk))

        # Remove the separator from the last chunk
        if (chunks and
            len(chunks[-1]) > separator_len and
            chunks[-1][-separator_len:] == separator
        ):
            chunks[-1] = chunks[-1][:-separator_len]
        return chunks
    
    
    if add_page_marker:
        max_words = max_words - PAGE_MARKER_SAFE_LEN
    
    chunks = _chunk(content, max_words, special_char_len)
    if add_page_marker:
        total_chunks = len(chunks)
        for i, chunk in enumerate(chunks):
            chunks[i] = chunk + _page_marker(i, total_chunks)
    return chunks
