## What I understand from your current setup

- **Existing project (Deep Intelligence API)**:
  - Pure **FastAPI** backend (no LangChain, no LangGraph).
  - Uses **Pandas** to load and aggregate data from Excel/CSV.
  - Builds a **data context** (pivot tables, filters, summaries) and then calls an **external LLM API (Perplexity `sonar-pro`)** with a crafted prompt.
  - Responses are further post‑processed (`format_ai_response`) to improve readability, then returned via:
    - `POST /chat` – general financial insights chatbot.
    - `POST /insights/chat` – insights endpoint with extra deterministic summaries.
  - Conversation history is passed as an array of messages; there is **no LangChain / LangGraph / “agent” framework** – the orchestration is handwritten with standard Python functions.
  - CORS is configured for a React‑style frontend, and the app exposes helper endpoints for filter options and data summary.

So, the **framework** here is:

- **Web framework**: FastAPI
- **Data**: Pandas
- **LLM orchestration**: Custom Python code (no LangChain, no LangGraph, no tool‑calling framework)
- **Model provider**: Perplexity API (remote), not your local models

You now want a **different chatbot** for an **ecommerce Shopify site**, to:

- Talk to users who are planning events (e.g. “50‑people party”).
- Ask follow‑up questions about the event (menu, starters only vs full meal, etc.).
- Look at your **product catalog** (plates, bowls, eco‑friendly spoons, etc. from DB).
- Compute required quantities (taking into account pack sizes like 30/60 per pack).
- Recommend a basket (bundles) and adjust based on user edits.
- **Write to the cart** and navigate to checkout when the user confirms.
- Run completely (or primarily) on your **local models** (Ollama‑style server on `localhost:11438`) and **ClickHouse** as the main analytical/product DB.

---

## Recommended overall architecture

### 1. High‑level components

- **Shopify storefront (frontend)**
  - Your existing Shopify theme / Hydrogen / custom React app.
  - A **chat widget** embedded on all relevant pages (product, collection, cart, dedicated “Party Planner” page).
  - Communicates with your backend via WebSocket or long‑polling HTTP (`/chat` endpoint).

- **AI Orchestration Backend (FastAPI, similar to your current project)**
  - Exposes:
    - `POST /chat` – main conversational endpoint.
    - `POST /session/{session_id}/event` – optional endpoint to log events or update context.
    - `POST /cart/apply` – endpoint to push recommended items to Shopify cart.
  - Connects to:
    - **Local LLM server** at `http://localhost:11438` (e.g. `qwen2.5:32b-instruct`, `llama3:70b`, etc.).
    - **ClickHouse** (local) for fast product and event‑planning queries.
    - **Shopify Storefront/Admin API** for cart and checkout operations.

- **Data Store: ClickHouse**
  - Tables:
    - `products` – denormalized product catalog for reasoning.
    - `packs` – mapping of product → units per pack, category, usage (plate, bowl, spoon, etc.).
    - `conversation_sessions` – optional, store chat transcripts and structured slots.
    - `recommendation_logs` – store recommended baskets, confirmation flags, final order info.

- **Local LLM stack**
  - You already have many models; for this use‑case:
    - **Primary assistant model**: `qwen2.5:32b-instruct` or `llama3:latest` (depending on latency vs quality).
    - **Lightweight intent parser / classifier** (optional): `llama3.2:latest` or `phi3:latest`.
    - **Embeddings** (for semantic search over products, FAQs): `nomic-embed-text:latest`.
  - Called via HTTP (`/api/chat`, `/api/generate`, `/api/embeddings`) on `localhost:11438`.

> **Framework choice**: given your existing FastAPI code and local models, I recommend **sticking with plain FastAPI + custom orchestration**, optionally adding **LangGraph later** if you want visual graph control. For v1, a clean, explicit Python “state machine + tools” will be simpler and more debuggable than pulling in LangChain.

---

## Conversation & state design

### 2. Core intents (“what the user is trying to do”)

At the backend, every user message should be classified into one of a small set of **intents**:

- `PLAN_EVENT`: plan items for a party/event.
- `ADJUST_PLAN`: modify quantities or items of an existing recommendation.
- `ASK_PRODUCT_DETAILS`: ask about a specific product or category.
- `CHECK_CART`: ask what’s already in the cart.
- `CHECKOUT`: proceed to checkout / finalize.
- `SMALL_TALK` / `OTHER`: greetings, out‑of‑scope questions.

You can implement this in two ways:

- **Rule‑based pre‑parser** (simple regex / keyword matching for “party”, “people”, “plate”, “checkout”).
- Or a **small LLM call** to a “classifier prompt” that outputs a tight JSON structure.

### 3. Slot‑filling for event planning

For `PLAN_EVENT`, maintain a **structured “party_plan” object** in server‑side session state:

```json
{
  "party_size": 50,
  "event_type": "birthday",
  "location": "home",
  "menu_type": "full_meal", 
  "courses": ["starters", "main", "dessert"],
  "disposables_needed": ["plates", "bowls", "spoons", "glasses", "napkins"],
  "budget_per_person": 150,
  "eco_preference": "high"
}
```

Backend logic should:

- Extract any available slots from the user's message using LLM + parsing (e.g. JSON output).
- Detect **missing slots** (e.g. menu_type, eco_preference).
- Ask targeted follow‑up questions:  
  “Are you serving only starters or a full meal?” / “Is eco‑friendly material a must‑have?”

State persists per `session_id` (e.g. stored in Redis or ClickHouse `conversation_sessions`).

### 4. Recommendation logic (deterministic + LLM explanation)

Once you have enough slots:

1. **Query ClickHouse** for candidate products:
   - Filter by category (`plates`, `bowls`, `spoons`).
   - Filter by tags (`eco-friendly`, `compostable`).
   - Filter by availability and price range.
2. For each category (e.g. plates):
   - Choose pack sizes to **minimize waste** and **respect budget**.  
     Example: 50 people, 30‑plate and 60‑plate packs.  
     Compute combinations that cover 50–60 plates with minimal extras (60 only, 2×30, etc.).
3. Build a recommended **basket**:
   ```json
   {
     "items": [
       {"product_id": "...", "variant_id": "...", "packs": 2, "units_per_pack": 30},
       {"product_id": "...", "variant_id": "...", "packs": 1, "units_per_pack": 50}
     ],
     "total_people": 50,
     "estimated_coverage": { "plates": 60, "bowls": 50, "spoons": 100 },
     "total_price": 2499
   }
   ```
4. Use LLM to **explain** the deterministic recommendation in a friendly way:
   - “For 50 guests with starters + main course, I recommend 3 packs of X (30 plates each)…”
   - Show a compact bullet list and ask:  
     “Would you like me to add this to your cart or adjust quantities first?”

Separation of concerns:

- **Math/pack calculation**: pure Python.
- **Product choice**: query + simple ranking (price, rating, eco score).
- **Natural language explanation & follow‑up**: LLM.

---

## Data & ClickHouse design

### 5. Product schema in ClickHouse

Example `products` table:

```sql
CREATE TABLE products (
  product_id String,
  shopify_product_id String,
  shopify_variant_id String,
  title String,
  description String,
  category String,         -- plates, bowls, spoons, etc.
  pack_size UInt16,        -- units per pack
  material String,         -- "areca leaf", "paper", "plastic"
  eco_score UInt8,         -- 1–5
  price_cents UInt32,
  tags Array(String),
  available Bool,
  updated_at DateTime
)
ENGINE = MergeTree
ORDER BY (category, eco_score, price_cents);
```

Optional `product_embeddings` table (for semantic search):

```sql
CREATE TABLE product_embeddings (
  product_id String,
  embedding Array(Float32)
)
ENGINE = MergeTree
ORDER BY product_id;
```

Pipeline:

- Periodically sync from Shopify via Admin API to ClickHouse.
- Generate embeddings using `nomic-embed-text:latest` for (title + description + tags).
- Use cosine similarity in ClickHouse or in Python for semantic search when queries are vague.

---

## LLM interaction pattern (with local models)

### 6. Calling your local models

- Use the existing server at `http://localhost:11438`:
  - `POST /api/chat` (or equivalent) with JSON payload:
    ```json
    {
      "model": "qwen2.5:32b-instruct",
      "messages": [
        {"role": "system", "content": "You are an event planning assistant for an eco-friendly tableware store..."},
        {"role": "user", "content": "I am planning a 50 people party..."}
      ],
      "stream": false
    }
    ```
- For **intent + slot extraction**, use a special prompt that forces JSON:
  - “Extract intent and slots from this message. Respond ONLY with JSON: {intent: ..., slots: {...}}”
  - Parse JSON in Python (with retry if malformed).

### 7. Tool‑like functions (without LangChain)

Instead of LangChain tools, implement a simple **internal tool registry**:

- `search_products_tool(params)`: queries ClickHouse.
- `compute_packs_tool(params)`: computes quantities.
- `update_cart_tool(params)`: calls Shopify API.

Or, for more autonomy:

- Allow the LLM to choose tools via a tight schema:
  - Backend logic: run a “planner” prompt -> get `{"tool": "search_products", "args": {...}}` -> execute tool -> feed result back to model in another call for NLG.
  - This gives you **agent‑like behavior** without LangChain.

LangGraph would make this more declarative, but for your first version, keeping the graph implicit in Python is simpler.

---

## Shopify integration flow

### 8. Cart & checkout integration

1. **User confirms recommendation**:
   - User: “Yes, add everything for 50 people and go to checkout.”
2. Backend:
   - Uses Shopify **Storefront API** or **Ajax cart API** to:
     - Create/update cart with recommended line items.
     - Return:
       ```json
       {
         "cart_id": "...",
         "checkout_url": "https://yourshop.myshopify.com/cart/c/..."
       }
       ```
3. Frontend chat widget:
   - Receives a structured payload from `/chat`, e.g.:
     ```json
     {
       "response": "Great, I’ve added everything to your cart...",
       "actions": [
         {
           "type": "NAVIGATE_TO_CHECKOUT",
           "checkout_url": "..."
         }
       ]
     }
     ```
   - If user clicks “Go to checkout”, the browser navigates to `checkout_url`.

### 9. Adjusting quantities

- When the user says:
  - “Make it 60 people” or “Reduce spoons to 80”.
- Backend:
  - Updates the `party_plan` object.
  - Re‑runs deterministic pack computation.
  - If items already in a Shopify cart:
    - Calls cart update API (update quantities / replace line items).
  - Sends updated summary back through `/chat`.

---

## FastAPI endpoints & flow (similar style to your existing code)

### 10. Proposed endpoints

- `POST /chat`
  - Request:
    ```json
    {
      "session_id": "uuid",
      "message": "I am planning a 50 people party...",
      "conversation_history": [...optional...]
    }
    ```
  - Steps:
    1. Load session state from ClickHouse/Redis (slots + last recommendation).
    2. Call LLM to classify intent + extract slots.
    3. If slots missing → respond with follow‑up question.
    4. If enough info:
       - Query ClickHouse products.
       - Compute packs.
       - Build basket & natural language explanation.
       - Return:
         ```json
         {
           "response": "For 50 guests, I recommend...",
           "data": {
             "basket": {...},
             "party_plan": {...}
           },
           "actions": [
             {"type": "OFFER_ADD_TO_CART"}
           ]
         }
         ```

- `POST /cart/apply`
  - Request:
    ```json
    {
      "session_id": "uuid",
      "basket": {...}
    }
    ```
  - Backend:
    - Uses Shopify Storefront/Admin API to create/update cart.
    - Returns checkout URL or cart ID.

- Optional:
  - `GET /session/{session_id}` – debug endpoint for state.
  - `POST /session/{session_id}/reset` – clear plan and start fresh.

Implementation style: mirror your existing FastAPI patterns, but swap out Perplexity for your **local LLMs** and Excel/pandas for **ClickHouse**.

---

## Step‑by‑step implementation plan

1. **Set up data & ClickHouse**
   - Define `products` schema in ClickHouse and populate it from Shopify.
   - (Optional) Create `product_embeddings` and run an initial embedding job via `nomic-embed-text:latest`.
   - Test queries for filtering by category, eco score, price, etc.

2. **Define party planning domain model**
   - Create Pydantic models for:
     - `PartyPlan` (slots).
     - `BasketItem`, `BasketRecommendation`.
     - `ChatRequest`, `ChatResponse` (similar to your existing project, but ecommerce‑oriented).

3. **Implement deterministic recommendation engine**
   - Given `PartyPlan` + product candidates, compute:
     - Required units per category.
     - Optimal pack combinations.
   - Unit‑test these functions independently from LLM.

4. **Integrate local LLM server**
   - Create a small client wrapper around `http://localhost:11438`:
     - `generate` function for normal chat responses.
     - `extract_intent_and_slots` function that forces JSON.
   - Add robust JSON parsing, logging, and retry on malformed outputs.

5. **Implement `/chat` endpoint**
   - Load or initialize session state.
   - Run intent + slot extraction.
   - Decide branch:
     - Need more info → ask follow‑ups.
     - Ready to recommend → call recommendation engine + LLM explanation.
   - Return both **text** and **structured actions** for the frontend.

6. **Implement Shopify cart integration**
   - Create `shopify_client.py` to:
     - Add/update line items in cart.
     - Generate checkout URL.
   - Implement `POST /cart/apply` and wire it from `/chat` when user confirms.

7. **Frontend chat widget**
   - Add a chat UI to your Shopify theme or React storefront.
   - Handle:
     - Displaying messages and loading state.
     - Rendering buttons for actions (“Add to cart”, “Edit quantities”, “Go to checkout”).
     - Calling `/chat` and `/cart/apply`.

8. **Logging, metrics, and iteration**
   - Log:
     - Inputs/outputs for `/chat`.
     - Final baskets and cart actions.
   - Analyze which recommendations are most accepted.
   - Refine prompts, packing heuristics, and UX.

---

## Framework recommendation summary

- **Web/API framework**: **FastAPI** (like your existing project).
- **Orchestration**: **Custom Python “state machine + tools”**, not LangChain/LangGraph for v1.
- **LLMs**: Your local models via `localhost:11438` (`qwen2.5:32b-instruct` or `llama3:latest` as main assistant).
- **Vector/analytics DB**: **ClickHouse** for product data, event logs, and (optionally) embeddings.
- **Shopify integration**: Native Storefront/Admin APIs for cart and checkout.

Once this is stable, you can optionally:

- Introduce **LangGraph** if you want more complex, branching flows and visual graph editing.
- Add **LangChain**‑style tools only if you need many external integrations and want their abstractions.
