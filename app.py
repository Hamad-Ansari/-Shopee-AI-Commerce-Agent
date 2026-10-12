"""ShopBot web dashboard (Phase 2). Talks to the same FastAPI backend over HTTP."""

import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="ShopBot — Phase 2 Demo", page_icon="🛒", layout="centered")
st.title("🛒 ShopBot — Shopee AI Commerce Agent")
st.caption("Phase 2 • mock catalog • approval queue • auth • persistent idempotency • audit log")

with st.sidebar:
    st.header("Session")
    user_id = st.text_input("User ID", value=st.session_state.get("user_id", "demo-user"))
    api_key = st.text_input(
        "API key", value=st.session_state.get("api_key", ""), type="password",
        help="Returned once when you register. Sent as 'Authorization: Bearer <key>'.",
    )
    role = st.selectbox("I am a…", ["buyer", "seller"])
    st.session_state["user_id"] = user_id
    st.session_state["api_key"] = api_key
    st.session_state["role"] = role
    st.divider()
    st.caption(f"Backend: {BACKEND_URL}")

    with st.expander("📝 Register (get an API key)"):
        with st.form("register_form"):
            r_user = st.text_input("New user ID", value=user_id)
            r_pass = st.text_input("Password (min 6 chars)", type="password")
            r_name = st.text_input("Display name", value="")
            r_role = st.selectbox("Role", ["buyer", "seller"])
            if st.form_submit_button("Register"):
                try:
                    r = httpx.post(
                        f"{BACKEND_URL}/auth/register",
                        json={"user_id": r_user, "password": r_pass, "name": r_name, "role": r_role},
                        timeout=30,
                    )
                    if r.status_code == 201:
                        data = r.json()
                        st.session_state["user_id"] = data["user_id"]
                        st.session_state["api_key"] = data["api_key"]
                        st.success(f"Registered! Your API key (shown once):\n\n`{data['api_key']}`")
                        st.rerun()
                    else:
                        st.error(r.json().get("detail", "Registration failed"))
                except Exception as e:  # noqa: BLE001
                    st.error(f"Could not reach backend: {e}")

if not api_key:
    st.warning("Enter your API key in the sidebar (or register) to chat with ShopBot.")

HEADERS = {"Authorization": f"Bearer {api_key}"} if api_key else {}

if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "pending_proposal_id" not in st.session_state:
    st.session_state["pending_proposal_id"] = None


def render_products(products):
    for i, p in enumerate(products, 1):
        with st.container(border=True):
            st.markdown(f"**{i}. {p['title']}**")
            st.caption(f"⭐ {p['rating']} ({p['reviews_count']} reviews) • {p['seller']} • {p['category']}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Price", f"PKR {p['price_pkr']:,.0f}")
            c2.metric("Stock", p["stock"])
            c3.metric("Delivery", f"{p['delivery_days']} day(s)")


for m in st.session_state["messages"]:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("products"):
            render_products(m["products"])

pid = st.session_state["pending_proposal_id"]
if pid:
    st.info("A proposal is waiting for your approval.")
    c1, c2 = st.columns(2)
    if c1.button("✅ Approve", use_container_width=True):
        r = httpx.post(f"{BACKEND_URL}/proposals/{pid}/approve", headers=HEADERS, timeout=30)
        data = r.json()
        st.session_state["messages"].append({"role": "assistant", "content": data.get("message", "")})
        st.session_state["pending_proposal_id"] = (
            data["proposal"]["id"] if data.get("needs_approval") and data.get("proposal") else None
        )
        st.rerun()
    if c2.button("❌ Reject", use_container_width=True):
        r = httpx.post(f"{BACKEND_URL}/proposals/{pid}/reject", headers=HEADERS, timeout=30)
        data = r.json()
        st.session_state["messages"].append({"role": "assistant", "content": data.get("message", "")})
        st.session_state["pending_proposal_id"] = None
        st.rerun()

prompt = st.chat_input("Message ShopBot… (e.g. find wireless earbuds under PKR 2,000)")
if prompt:
    st.session_state["messages"].append({"role": "user", "content": prompt})
    try:
        r = httpx.post(
            f"{BACKEND_URL}/chat",
            json={"user_id": user_id, "message": prompt, "role": role},
            headers=HEADERS,
            timeout=60,
        )
        if r.status_code == 401:
            data = {"message": "🔑 Unauthorized — check your API key in the sidebar."}
        elif r.status_code == 403:
            data = {"message": "🚫 The user ID doesn't match your API key's account."}
        else:
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        data = {"message": f"⚠️ Could not reach the backend at {BACKEND_URL}. Is uvicorn running? ({e})"}
    st.session_state["messages"].append(
        {"role": "assistant", "content": data.get("message", ""), "products": data.get("products", [])}
    )
    if data.get("needs_approval") and data.get("proposal"):
        st.session_state["pending_proposal_id"] = data["proposal"]["id"]
    st.rerun()

with st.expander("📦 My orders"):
    try:
        r = httpx.get(f"{BACKEND_URL}/orders", headers=HEADERS, timeout=30)
        if r.status_code == 200:
            orders = r.json().get("orders", [])
            if not orders:
                st.caption("No orders yet.")
            for o in orders:
                st.markdown(f"**{o['marketplace_order_id'] or o['id']}** — {o['status']} — PKR {o['total_pkr']:,.0f}")
        else:
            st.caption("Sign in to view your orders.")
    except Exception as e:  # noqa: BLE001
        st.caption(f"Could not load orders: {e}")

with st.expander("🧾 My audit trail"):
    try:
        r = httpx.get(f"{BACKEND_URL}/audit", params={"limit": 25}, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            events = r.json().get("events", [])
            if not events:
                st.caption("No audit events yet.")
            for e in reversed(events):
                st.caption(f"{e['created_at'][:19]} • `{e['event_type']}`")
        else:
            st.caption("Sign in to view your audit trail.")
    except Exception as e:  # noqa: BLE001
        st.caption(f"Could not load audit trail: {e}")
