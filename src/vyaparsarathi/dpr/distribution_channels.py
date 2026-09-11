"""Category-generic distribution-channel guidance for the DPR (CLAUDE.md §8,
§25 Phase 8) — the SIH26091 judge feedback's Priority 5 gap ("show primary
customer channel, secondary channels, B2B potential, supply channel, and the
risk of depending on one").

Mirrors `market/opportunity_config.py`'s `_ASSET_RELEVANCE`/`_CAPITAL_BANDS`
convention: a plain module-level dict, hand-authored, each entry a
*qualitative* claim in the same class as `market/relationships.py` — being
wrong yields a wrong generic sentence, not a wrong number. A category with no
row here gets `_GENERIC_FALLBACK`, not an error (the same "flagged, not
renormalised away" spirit).

Every string is a GENERIC claim about how businesses of this category
typically reach customers and source stock — never a claim about a specific
real supplier, buyer, or channel discovered at this location. Location-
specific distribution facts are not something this system observes; stating
one would be exactly the kind of fabrication CLAUDE.md §30 forbids.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from vyaparsarathi.models.taxonomy import BusinessCategory


class DistributionChannelGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    primary_channel: str
    secondary_channels: tuple[str, ...]
    b2b_potential: str
    supply_channel: str
    single_channel_risk_note: str


_GENERIC_FALLBACK = DistributionChannelGuidance(
    primary_channel="Walk-in retail to nearby households.",
    secondary_channels=("Word of mouth in nearby settlements.",),
    b2b_potential="Not assessed for this business category.",
    supply_channel="Local wholesalers or distributors, where available.",
    single_channel_risk_note=(
        "Relying on a single walk-in customer base and a single supplier leaves the "
        "business exposed if either weakens; a second option on each side, even one "
        "rarely used, reduces that risk."
    ),
)

# category (string value) -> guidance. [assumption] — hand-authored generic
# trade knowledge, not a location-specific finding. Populated for the
# categories a rural entrepreneur most often proposes; every other category
# falls back to `_GENERIC_FALLBACK` above, which is deliberately generic
# rather than a guess dressed up as specific.
_CHANNELS_BY_CATEGORY: dict[str, DistributionChannelGuidance] = {
    "grocery": DistributionChannelGuidance(
        primary_channel="Walk-in retail to nearby households.",
        secondary_channels=(
            "Nearby villages/hamlets without their own grocery store.",
            "Weekly haat (market day) footfall.",
            "Phone or WhatsApp orders for regular customers.",
        ),
        b2b_potential="Small tea stalls or eateries buying staples in bulk.",
        supply_channel="Local wholesalers, mandi traders, or a distributor's delivery round.",
        single_channel_risk_note=(
            "Depending on one wholesale supplier for staples risks a stock-out if that "
            "supplier's own supply is disrupted; keeping a second supplier relationship, "
            "even a rarely-used one, reduces that risk."
        ),
    ),
    "general_store": DistributionChannelGuidance(
        primary_channel="Walk-in retail to nearby households.",
        secondary_channels=(
            "Nearby villages/hamlets with no comparable shop.",
            "Weekly haat (market day) footfall.",
        ),
        b2b_potential="Small local shops restocking a narrow item at short notice.",
        supply_channel="A mix of wholesalers per product category (no single distributor).",
        single_channel_risk_note=(
            "A general store's breadth of stock already spreads supplier risk across "
            "categories; the bigger risk is over-relying on one or two fast-moving lines "
            "for most of the day's footfall."
        ),
    ),
    "dairy": DistributionChannelGuidance(
        primary_channel="Daily walk-in/doorstep sale of milk and dairy items.",
        secondary_channels=(
            "Standing morning/evening delivery rounds to regular households.",
            "Sale to a tea stall, sweet shop, or small eatery.",
        ),
        b2b_potential="Tea stalls, sweet shops, and small eateries as recurring bulk buyers.",
        supply_channel="Own herd, or collection from nearby smallholder producers.",
        single_channel_risk_note=(
            "Milk is highly perishable and cold-chain dependent — a single delivery route "
            "or a single collection source going down risks spoiled, unsellable stock the "
            "same day, more acutely than for non-perishable goods."
        ),
    ),
    "pharmacy": DistributionChannelGuidance(
        primary_channel="Walk-in retail, largely prescription- and need-driven.",
        secondary_channels=(
            "Referral from a local doctor or health worker.",
            "Standing orders for chronic-medication customers.",
        ),
        b2b_potential="Small clinics or health workers buying in bulk for dispensing.",
        supply_channel="Licensed pharmaceutical distributors/stockists (regulated channel).",
        single_channel_risk_note=(
            "A pharmacy's supply channel is regulated and typically concentrated in one or "
            "two licensed distributors; a delay from that distributor can leave essential "
            "medicines out of stock with no informal substitute."
        ),
    ),
    "food_stall": DistributionChannelGuidance(
        primary_channel="Walk-up footfall at a fixed or semi-fixed spot.",
        secondary_channels=(
            "Repeat custom from nearby workplaces/institutions.",
            "Market-day or event-day footfall spikes.",
        ),
        b2b_potential="Bulk/catering orders for small local gatherings.",
        supply_channel="Daily purchase of fresh ingredients from a local market/mandi.",
        single_channel_risk_note=(
            "Revenue is concentrated in footfall at one location and time of day; a change "
            "in foot traffic there (a diverted road, a competing stall) affects the whole "
            "business at once."
        ),
    ),
    "restaurant": DistributionChannelGuidance(
        primary_channel="Dine-in walk-in customers.",
        secondary_channels=(
            "Takeaway and phone/WhatsApp orders.",
            "Local delivery apps, where coverage reaches this location.",
        ),
        b2b_potential="Catering for local events and small gatherings.",
        supply_channel="Daily/periodic purchase from a local mandi or wholesale supplier.",
        single_channel_risk_note=(
            "Dine-in-only revenue is exposed to anything that keeps customers from "
            "physically visiting (weather, a bad season, road access); a second channel "
            "(takeaway/delivery) reduces that single point of dependence."
        ),
    ),
    "agri_input": DistributionChannelGuidance(
        primary_channel="Walk-in sale to nearby farmers, seasonal (sowing/spraying windows).",
        secondary_channels=("Farm-gate delivery for bulkier orders.",),
        b2b_potential="Larger farmer groups or cooperatives buying in bulk before a season.",
        supply_channel="Authorised distributors for seed/fertiliser/pesticide brands.",
        single_channel_risk_note=(
            "Demand is concentrated in short seasonal windows tied to the local cropping "
            "calendar; a supply delay right before sowing/spraying season has an outsized "
            "impact compared to the same delay at another time of year."
        ),
    ),
    "food_processing": DistributionChannelGuidance(
        primary_channel="Custom milling/processing for walk-in customers bringing their own grain.",
        secondary_channels=("Sale of the processed output (flour/oil/dal) directly.",),
        b2b_potential="Local grocery/general stores buying processed output wholesale.",
        supply_channel="Local mandi/wholesale purchase of raw grain, when processing for resale.",
        single_channel_risk_note=(
            "Revenue mixes a service fee (custom milling) with product sale (processed "
            "output); depending on only one of the two narrows the customer base "
            "unnecessarily."
        ),
    ),
    "livestock_services": DistributionChannelGuidance(
        primary_channel="On-farm service calls or feed sale, not a fixed storefront.",
        secondary_channels=("Referral within the local livestock-keeping community.",),
        b2b_potential="Larger livestock owners or a local dairy cooperative.",
        supply_channel="Wholesale purchase of feed/veterinary supplies from a distributor.",
        single_channel_risk_note=(
            "Much of this trade depends on word of mouth within a livestock-keeping "
            "community rather than passing footfall; a slow start in building that "
            "reputation affects the whole customer pipeline at once."
        ),
    ),
    "hardware": DistributionChannelGuidance(
        primary_channel="Walk-in retail to households and local tradespeople.",
        secondary_channels=("Referral from local masons/contractors/electricians.",),
        b2b_potential="Local contractors and tradespeople buying materials for a job.",
        supply_channel="Wholesale distributors per product line (paint, pipes, tools, etc.).",
        single_channel_risk_note=(
            "A hardware shop's stock spans many unrelated product lines from different "
            "suppliers; the practical risk is holding too much capital in slow-moving "
            "stock rather than a single-supplier dependence."
        ),
    ),
    "clothing": DistributionChannelGuidance(
        primary_channel="Walk-in retail, often seasonal (festivals, weddings, school terms).",
        secondary_channels=("Local tailoring/alteration referrals.",),
        b2b_potential="Not typically significant for a small rural clothing retailer.",
        supply_channel="Wholesale purchase from a nearby town's cloth/garment market.",
        single_channel_risk_note=(
            "Demand is seasonal and concentrated around specific occasions; a poor season "
            "or a missed festival window affects a large share of annual revenue at once."
        ),
    ),
    "mobile_electronics": DistributionChannelGuidance(
        primary_channel="Walk-in retail and repair service.",
        secondary_channels=("Referral for repair work.",),
        b2b_potential="Not typically significant at this scale.",
        supply_channel="Authorised distributors/wholesalers for handsets and accessories.",
        single_channel_risk_note=(
            "Fast-changing models and price points mean unsold stock can lose value "
            "quickly; over-committing capital to one supplier's current line-up is a "
            "sharper risk here than for slower-changing goods."
        ),
    ),
    "tailoring": DistributionChannelGuidance(
        primary_channel="Walk-in custom stitching/alteration for individual customers.",
        secondary_channels=("Repeat custom around festivals/weddings/school-uniform season.",),
        b2b_potential="Local clothing shops outsourcing alteration work.",
        supply_channel=(
            "Local purchase of thread/lining/notions; cloth is usually customer-supplied."
        ),
        single_channel_risk_note=(
            "Revenue is concentrated in individual walk-in custom, often seasonal; a slow "
            "season directly reduces income with no bulk/wholesale channel to fall back on."
        ),
    ),
    "salon": DistributionChannelGuidance(
        primary_channel="Walk-in personal-care service.",
        secondary_channels=("Repeat/regular customers on a routine visit cycle.",),
        b2b_potential="Not typically significant at this scale.",
        supply_channel="Wholesale purchase of consumables (products, tools) from a distributor.",
        single_channel_risk_note=(
            "Revenue depends entirely on footfall at one fixed location and the "
            "operator's own working hours; there is no wholesale or bulk channel to "
            "offset a quiet period."
        ),
    ),
}


def distribution_channels_for(category: BusinessCategory) -> DistributionChannelGuidance:
    """Generic, category-level guidance only — never a claim about a specific
    real supplier, buyer, or channel at this location. A category absent from
    the table returns `_GENERIC_FALLBACK`, not an error."""
    return _CHANNELS_BY_CATEGORY.get(category.value, _GENERIC_FALLBACK)


__all__ = ["DistributionChannelGuidance", "distribution_channels_for"]
