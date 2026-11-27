"""MCP server exposing the DarGlobal + Wasalt dataset as tools.

Any MCP client (Claude Code, Claude Desktop, Cursor, ...) can query the data with its
own model; no OpenRouter key or quota is used.

Two ways to run it:
  * Remote (Streamable HTTP): mounted in the FastAPI app at /mcp (see app/main.py)
        claude mcp add --transport http estate-assistant https://estate-assistant.onrender.com/mcp
  * Local (stdio):
        python -m app.mcp_server
"""
import os
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from app.data_store import DataStore
from app.retriever import Retriever

INSTRUCTIONS = """Real-estate data scraped (Nov 2026) from two public websites:
- DarGlobal (darglobal.co.uk): 38 luxury projects in UAE, Oman, Saudi Arabia, Qatar, Spain and UK,
  plus company, brand-partner and press pages. DarGlobal does not publish prices ("price on request").
- Wasalt (wasalt.sa): ~60,000 Saudi sale/rent listings from the public sitemap (type, area,
  bedrooms, facing, street width; no prices or cities).
Use search_listings for structured filters, market_stats for Wasalt counts/sizes,
semantic_search for descriptive questions, get_listing for full details. Cite the returned URLs."""

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

Str = Annotated[str | None, Field(default=None)]
Int = Annotated[int | None, Field(default=None)]
Num = Annotated[float | None, Field(default=None)]


def allowed_hosts():
    default = "estate-assistant.onrender.com,localhost:*,127.0.0.1:*"
    return [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", default).split(",") if h.strip()]


def build_mcp(store: DataStore, retriever: Retriever) -> MCPServer:
    mcp = MCPServer(name="estate-assistant", title="Estate Assistant (DarGlobal + Wasalt)",
                    instructions=INSTRUCTIONS, version="1.0.0",
                    website_url="https://estate-assistant.onrender.com")

    @mcp.tool(annotations=READ_ONLY)
    def dataset_overview() -> dict:
        """Counts of what the dataset contains (projects per country, listings, pages)."""
        return store.overview()

    @mcp.tool(annotations=READ_ONLY)
    def search_listings(
        source: Annotated[Literal["darglobal", "wasalt"] | None, Field(default=None)] = None,
        country: Annotated[str | None, Field(description="e.g. UAE, Oman, Saudi Arabia, UK, Spain, Qatar")] = None,
        city: Annotated[str | None, Field(description="city or district, e.g. Dubai, Riyadh, Muscat")] = None,
        listing_type: Annotated[str | None, Field(description="sale, rent, or off-plan")] = None,
        property_type: Annotated[str | None, Field(description="e.g. apartment, villa, land")] = None,
        min_bedrooms: Int = None, max_bedrooms: Int = None,
        min_price: Num = None, max_price: Num = None,
        min_area_sqm: Num = None, max_area_sqm: Num = None,
        keyword: Annotated[str | None, Field(description="words that must appear, e.g. 'golf', 'Trump'")] = None,
        sort_by: Annotated[Literal["price_asc", "price_desc", "newest"] | None, Field(default=None)] = None,
        limit: Annotated[int, Field(ge=1, le=15)] = 8,
    ) -> dict:
        """Filter DarGlobal projects and detailed Wasalt listings by structured criteria.
        Returns a summary (match count, price range) and compact result records with URLs."""
        return store.search_listings(
            source=source, country=country, city=city, listing_type=listing_type,
            property_type=property_type, min_bedrooms=min_bedrooms, max_bedrooms=max_bedrooms,
            min_price=min_price, max_price=max_price, min_area_sqm=min_area_sqm,
            max_area_sqm=max_area_sqm, keyword=keyword, sort_by=sort_by, limit=limit)

    @mcp.tool(annotations=READ_ONLY)
    def market_stats(
        listing_type: Annotated[Literal["sale", "rent"] | None, Field(default=None)] = None,
        property_type: Annotated[str | None, Field(
            description="apartment, villa, land, floor, building, rest house, room")] = None,
        min_area_sqm: Num = None, max_area_sqm: Num = None,
        bedrooms: Int = None,
        facing: Annotated[str | None, Field(description="north, south, east, west, north east, ...")] = None,
        group_by: Annotated[Literal["listing_type", "property_type", "bedrooms", "facing"] | None,
                            Field(default=None)] = None,
    ) -> dict:
        """Counts, area statistics and grouped shares across ~60,000 Wasalt (Saudi) listings.
        The sitemap data has no prices or cities."""
        return store.market_stats(listing_type=listing_type, property_type=property_type,
                                  min_area_sqm=min_area_sqm, max_area_sqm=max_area_sqm,
                                  bedrooms=bedrooms, facing=facing, group_by=group_by)

    @mcp.tool(annotations=READ_ONLY)
    def semantic_search(
        query: Annotated[str, Field(description="natural-language question or topic")],
        source: Annotated[Literal["darglobal", "wasalt"] | None, Field(default=None)] = None,
        k: Annotated[int, Field(ge=1, le=10)] = 6,
    ) -> list[dict]:
        """Search project descriptions and DarGlobal pages (about, why-invest, partners, press) by
        meaning. Returns text passages with title, URL and similarity score."""
        return retriever.search(query, k=k, source=source)

    @mcp.tool(annotations=READ_ONLY)
    def get_listing(
        listing_id: Annotated[str, Field(description="id from search results, e.g. darglobal-the-astera")],
    ) -> dict:
        """Full details of one project or listing: description, amenities, features, URL."""
        return store.get_listing(listing_id)

    return mcp


def http_app(mcp: MCPServer):
    """Streamable HTTP ASGI app (stateless + JSON responses: simple and scale-friendly)."""
    return mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts(),
            allowed_origins=["https://estate-assistant.onrender.com", "http://localhost:*",
                             "http://127.0.0.1:*"]))


if __name__ == "__main__":  # local stdio server
    build_mcp(DataStore(), Retriever()).run("stdio")
