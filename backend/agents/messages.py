HELP_MESSAGE = (
    "👋 Hi! I'm ShopBot, your commerce assistant.\n"
    "• Buyer: \"find wireless earbuds under PKR 2,000\", then \"buy the first one\", then APPROVE.\n"
    "• Seller: \"list a water bottle at PKR 500 with stock 20\", then APPROVE to publish.\n"
    "• Also: \"track my order\" • \"check stock of power bank\" • \"my sales summary\""
)

CLARIFY_MESSAGE = (
    "🤔 I'm not sure what you'd like. Try searching for a product, buying one, "
    "or (as a seller) creating a listing."
)

PENDING_NUDGE = "Please reply APPROVE or REJECT to answer the pending proposal."


def format_price(v: float) -> str:
    return f"PKR {v:,.0f}"


def card_lines(p: dict, i: int) -> str:
    return (
        f"{i}. {p['title']}\n"
        f"   {format_price(p['price_pkr'])} • ⭐ {p['rating']} • {p['seller']}\n"
        f"   Stock: {p['stock']} • Delivery: {p['delivery_days']} day(s) • "
        f"Shipping: {format_price(p['shipping_fee_pkr'])}"
    )


def format_order_proposal(proposal) -> str:
    item = proposal.items[0]
    return (
        f"🧾 Order proposal v{proposal.version} (#{proposal.id[:8]})\n"
        f"• {item['quantity']}x {item['title']} @ {format_price(item['unit_price_pkr'])}\n"
        f"• Seller: {item['seller']}\n"
        f"• Shipping: {format_price(proposal.shipping_fee_pkr)}\n"
        f"• Total: {format_price(proposal.total_pkr)}"
    )


def format_listing_preview(listing) -> str:
    return (
        f"📝 Listing draft (#{listing.id[:8]})\n"
        f"• Title: {listing.title}\n"
        f"• Category: {listing.category}\n"
        f"• Price: {format_price(listing.price_pkr)}\n"
        f"• Stock: {listing.stock} unit(s)\n"
        f"• Description: {listing.description or '-'}"
    )
