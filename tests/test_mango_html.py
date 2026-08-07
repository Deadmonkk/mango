"""Tests for terminalq.mango.html — HTML tag stripping and table parsing."""

import pytest

from terminalq.mango.html import BROWSER_HEADERS, strip_tags, table_rows


class TestBrowserHeaders:
    """Verify BROWSER_HEADERS contains realistic browser identification."""

    def test_browser_headers_has_user_agent(self):
        """BROWSER_HEADERS includes a non-default User-Agent."""
        assert "User-Agent" in BROWSER_HEADERS
        user_agent = BROWSER_HEADERS["User-Agent"]
        # Real browser user agents mention Mozilla, WebKit, or Chrome.
        assert any(
            keyword in user_agent
            for keyword in ["Mozilla", "WebKit", "Chrome", "Safari"]
        ), f"User-Agent looks too generic: {user_agent}"

    def test_browser_headers_has_accept_headers(self):
        """BROWSER_HEADERS includes Accept and Accept-Language."""
        assert "Accept" in BROWSER_HEADERS
        assert "Accept-Language" in BROWSER_HEADERS
        # Accept-Language should mention at least one language.
        assert "en" in BROWSER_HEADERS["Accept-Language"]

    def test_browser_headers_is_dict(self):
        """BROWSER_HEADERS is a dict of strings."""
        assert isinstance(BROWSER_HEADERS, dict)
        for key, value in BROWSER_HEADERS.items():
            assert isinstance(key, str)
            assert isinstance(value, str)


class TestStripTags:
    """Test HTML tag removal, entity decoding, and whitespace normalization."""

    def test_strip_simple_tags(self):
        """Remove opening and closing tags from simple HTML."""
        # Arrange
        html = "<div>hello</div>"
        # Act
        result = strip_tags(html)
        # Assert
        assert result == "hello"

    def test_strip_nested_tags(self):
        """Remove nested tags from HTML."""
        # Arrange
        html = "<div><span>hello</span></div>"
        # Act
        result = strip_tags(html)
        # Assert
        assert result == "hello"

    def test_decode_html_entities(self):
        """Decode HTML entities like &amp;, &lt;, &gt;."""
        # Arrange
        html = "&amp; &lt; &gt;"
        # Act
        result = strip_tags(html)
        # Assert
        assert result == "& < >"

    def test_decode_nbsp_entity(self):
        """Decode &nbsp; as a space."""
        # Arrange
        html = "word1&nbsp;word2"
        # Act
        result = strip_tags(html)
        # Assert
        assert result == "word1 word2"

    def test_decode_numeric_entity(self):
        """Decode numeric HTML entities like &#160; (non-breaking space)."""
        # Arrange
        html = "word1&#160;word2"
        # Act
        result = strip_tags(html)
        # Assert
        assert result == "word1 word2"

    def test_collapse_whitespace(self):
        """Collapse consecutive whitespace to single spaces."""
        # Arrange
        html = "hello   world\n\t   test"
        # Act
        result = strip_tags(html)
        # Assert
        assert result == "hello world test"

    def test_strip_and_decode_and_collapse(self):
        """Strip tags, decode entities, and collapse whitespace together."""
        # Arrange
        html = "<div>hello&nbsp;  <b>world</b>  </div>"
        # Act
        result = strip_tags(html)
        # Assert
        assert result == "hello world"

    def test_uppercase_tags(self):
        """Strip uppercase tag names like <DIV>, </DIV>."""
        # Arrange
        html = "<DIV>HELLO</DIV>"
        # Act
        result = strip_tags(html)
        # Assert
        assert result == "HELLO"

    def test_tags_with_attributes(self):
        """Strip tags that have attributes."""
        # Arrange
        html = '<div class="container" id="main">text</div>'
        # Act
        result = strip_tags(html)
        # Assert
        assert result == "text"

    def test_self_closing_tag(self):
        """Handle self-closing tags like <br />."""
        # Arrange
        html = "line1<br />line2"
        # Act
        result = strip_tags(html)
        # Assert
        assert result == "line1 line2"

    def test_unclosed_tag_tolerates_malformed_html(self):
        """Tolerate malformed HTML with unclosed tags."""
        # Arrange
        html = "<div>text<br>"
        # Act
        result = strip_tags(html)
        # Assert
        # Expects the <div> and <br> to be stripped, leaving "text".
        assert result == "text"

    def test_empty_string_returns_empty(self):
        """Empty string input returns empty string."""
        # Arrange
        html = ""
        # Act
        result = strip_tags(html)
        # Assert
        assert result == ""

    def test_non_string_returns_empty(self):
        """Non-string input returns empty string instead of raising."""
        # Arrange
        html = None  # type: ignore
        # Act
        result = strip_tags(html)
        # Assert
        assert result == ""

    def test_leading_trailing_whitespace_stripped(self):
        """Leading and trailing whitespace is removed."""
        # Arrange
        html = "  <p>  hello world  </p>  "
        # Act
        result = strip_tags(html)
        # Assert
        assert result == "hello world"


class TestTableRows:
    """Test HTML table parsing into rows and cells."""

    def test_simple_table_parsing(self):
        """Parse a simple HTML table into rows of cells."""
        # Arrange
        html = """
        <table>
            <tr><td>Jan</td><td>100</td></tr>
            <tr><td>Feb</td><td>200</td></tr>
        </table>
        """
        # Act
        result = table_rows(html)
        # Assert
        assert result == [
            ["Jan", "100"],
            ["Feb", "200"],
        ]

    def test_table_with_header_rows(self):
        """Include <th> header rows in the output."""
        # Arrange
        html = """
        <table>
            <tr><th>Month</th><th>Value</th></tr>
            <tr><td>Jan</td><td>100</td></tr>
        </table>
        """
        # Act
        result = table_rows(html)
        # Assert
        assert len(result) == 2
        assert result[0] == ["Month", "Value"]
        assert result[1] == ["Jan", "100"]

    def test_strip_nested_tags_in_cells(self):
        """Strip nested tags inside table cells."""
        # Arrange
        html = """
        <table>
            <tr><td><b>bold</b> text</td><td><a href="#">link</a></td></tr>
        </table>
        """
        # Act
        result = table_rows(html)
        # Assert
        assert result == [["bold text", "link"]]

    def test_decode_entities_in_cells(self):
        """Decode HTML entities in table cells."""
        # Arrange
        html = """
        <table>
            <tr><td>A &amp; B</td><td>10&nbsp;dollars</td></tr>
        </table>
        """
        # Act
        result = table_rows(html)
        # Assert
        assert result == [["A & B", "10 dollars"]]

    def test_collapse_whitespace_in_cells(self):
        """Collapse whitespace in table cells."""
        # Arrange
        html = """
        <table>
            <tr><td>  multiple   spaces  </td><td>
                newlines
                and tabs
            </td></tr>
        </table>
        """
        # Act
        result = table_rows(html)
        # Assert
        assert result == [["multiple spaces", "newlines and tabs"]]

    def test_uppercase_table_tags(self):
        """Handle uppercase table tags like <TR>, <TD>."""
        # Arrange
        html = """
        <TABLE>
            <TR><TD>Cell1</TD><TD>Cell2</TD></TR>
            <TR><TD>Cell3</TD><TD>Cell4</TD></TR>
        </TABLE>
        """
        # Act
        result = table_rows(html)
        # Assert
        assert result == [
            ["Cell1", "Cell2"],
            ["Cell3", "Cell4"],
        ]

    def test_mixed_case_tags(self):
        """Handle mixed-case tags like <Tr>, <Td>."""
        # Arrange
        html = """
        <table>
            <Tr><Td>Cell1</Td><Td>Cell2</Td></Tr>
        </table>
        """
        # Act
        result = table_rows(html)
        # Assert
        assert result == [["Cell1", "Cell2"]]

    def test_attributes_in_tags(self):
        """Handle table tags with attributes."""
        # Arrange
        html = """
        <table id="data" class="results">
            <tr class="header"><th>Name</th></tr>
            <tr data-id="1"><td class="left">Value</td></tr>
        </table>
        """
        # Act
        result = table_rows(html)
        # Assert
        assert result == [["Name"], ["Value"]]

    def test_attribute_with_gt_in_quotes(self):
        """Tolerate attributes containing `>` inside quotes."""
        # Arrange
        html = """
        <table>
            <tr data-compare="x > y"><td>Cell</td></tr>
        </table>
        """
        # Act
        result = table_rows(html)
        # Assert
        # Should parse the cell correctly even with `>` in the attribute.
        assert result == [["Cell"]]

    def test_extra_whitespace_in_tags(self):
        """Tolerate extra whitespace inside tag definitions."""
        # Arrange
        html = """
        <table>
            <  tr  ><  td  >Cell1</  td  ><  td  >Cell2</  td  ></  tr  >
        </table>
        """
        # Act
        result = table_rows(html)
        # Assert
        assert result == [["Cell1", "Cell2"]]

    def test_nested_tables_yield_inner_row_separately(self):
        """Nested tables produce the inner row as its own row.

        Deliberate trade-off. Delimiting on opening tags is what lets unclosed
        <td>/<tr> parse at all, and that costs strict nesting: the outer cell
        holding the inner table reads empty and the inner row is emitted
        separately. Unclosed cells are far more common in scraped pages than
        nested tables, and callers filter rows by expected column count, so an
        extra row is benign where a silently empty result would not be.
        """
        # Arrange
        html = (
            "<table><tr><td>Outer1</td><td>"
            "<table><tr><td>Inner1</td></tr></table>"
            "</td></tr></table>"
        )
        # Act
        result = table_rows(html)
        # Assert
        assert result == [["Outer1", ""], ["Inner1"]]

    def test_unclosed_cell_tags(self):
        """Unclosed <td> is legal HTML that real scraped pages emit."""
        # Arrange
        html = "<table><tr><td>Cell1<td>Cell2</table>"
        # Act
        result = table_rows(html)
        # Assert
        assert result == [["Cell1", "Cell2"]]

    def test_unclosed_row_tags(self):
        """A new <tr> ends the previous row even without </tr>."""
        # Arrange
        html = "<table><tr><td>A<td>B<tr><td>C<td>D</table>"
        # Act
        result = table_rows(html)
        # Assert
        assert result == [["A", "B"], ["C", "D"]]

    def test_attribute_containing_gt_inside_quotes(self):
        """A '>' inside a quoted attribute must not end the tag early."""
        # Arrange
        html = '<table><tr><td><a href="x>y">Link</a></td></tr></table>'
        # Act
        result = table_rows(html)
        # Assert
        assert result == [["Link"]]

    def test_no_table_returns_empty_list(self):
        """Return empty list if no tables are found."""
        # Arrange
        html = "<html><body>No tables here</body></html>"
        # Act
        result = table_rows(html)
        # Assert
        assert result == []

    def test_empty_table_returns_empty_list(self):
        """Return empty list if table has no rows."""
        # Arrange
        html = "<table></table>"
        # Act
        result = table_rows(html)
        # Assert
        assert result == []

    def test_empty_string_returns_empty_list(self):
        """Empty string input returns empty list."""
        # Arrange
        html = ""
        # Act
        result = table_rows(html)
        # Assert
        assert result == []

    def test_non_string_returns_empty_list(self):
        """Non-string input returns empty list instead of raising."""
        # Arrange
        html = None  # type: ignore
        # Act
        result = table_rows(html)
        # Assert
        assert result == []

    def test_row_with_no_cells_skipped(self):
        """Skip rows that have no cells."""
        # Arrange
        html = """
        <table>
            <tr></tr>
            <tr><td>Cell1</td></tr>
            <tr>  </tr>
            <tr><td>Cell2</td></tr>
        </table>
        """
        # Act
        result = table_rows(html)
        # Assert
        # Empty rows should not appear in the output.
        assert result == [["Cell1"], ["Cell2"]]

    def test_real_world_multpl_format(self):
        """Parse a table similar to multpl.com's format."""
        # Arrange
        html = """
        <table id="datatable">
            <tr><th>Date</th><th>Value</th></tr>
            <tr><td class="left">Jun 10, 2026</td><td class="right">37.50</td></tr>
            <tr><td class="left">May 1, 2026</td><td class="right">36.20</td></tr>
        </table>
        """
        # Act
        result = table_rows(html)
        # Assert
        assert len(result) == 3
        assert result[0] == ["Date", "Value"]
        assert result[1] == ["Jun 10, 2026", "37.50"]
        assert result[2] == ["May 1, 2026", "36.20"]

    def test_real_world_aaii_format(self):
        """Parse a sentiment table similar to AAII's format."""
        # Arrange
        html = """
        <table>
            <tr><th>Week</th><th>Bullish</th><th>Neutral</th><th>Bearish</th></tr>
            <tr><td>Aug 6</td><td>45.3%</td><td>27.1%</td><td>27.6%</td></tr>
            <tr><td>Jul 30</td><td>42.1%</td><td>29.4%</td><td>28.5%</td></tr>
        </table>
        """
        # Act
        result = table_rows(html)
        # Assert
        assert len(result) == 3
        assert result[0] == ["Week", "Bullish", "Neutral", "Bearish"]
        assert result[1] == ["Aug 6", "45.3%", "27.1%", "27.6%"]
        assert result[2] == ["Jul 30", "42.1%", "29.4%", "28.5%"]
