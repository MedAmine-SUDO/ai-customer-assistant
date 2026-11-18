from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.deps import DbSession
from app.api.routes import admin, chat, internal, whatsapp_bot
from app.core.config import settings
from app.tenants import TENANTS

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

DESCRIPTION = f"""
An AI assistant that answers customers 24/7 from the business's own documents (FAQ, price list,
catalog), in French, English and Arabic, captures leads into Google Sheets, alerts the team and
follows up 24 hours later. Two demo businesses: a dental clinic in Sousse (`dentiste`) and a
furniture shop (`meubles`).

### Try it
1. `POST /api/v1/chat` with `{{"tenant": "dentiste", "message": "Combien coûte un détartrage ?"}}`.
   Send the returned `conversation_id` with the next messages.
2. Say you want an appointment and give a name and a phone number: `lead_captured` turns true.
3. `POST /api/v1/admin/login` with `{{"password": "{settings.admin_password if settings.demo_mode else "…"}"}}`,
   click **Authorize** with the token, then `GET /api/v1/admin/leads`.
4. `GET /api/v1/stats`: what it costs per conversation, measured.

LLM in use: **{settings.llm_provider}** ({settings.chat_model if settings.llm_provider == "openai" else "keyless mock"}).
"""

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=DESCRIPTION,
    swagger_ui_parameters={"persistAuthorization": True, "docExpansion": "none"},
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(admin.protected, prefix="/api/v1")
app.include_router(whatsapp_bot.router)
app.include_router(internal.router)


@app.get("/health", tags=["meta"])
def health(db: DbSession):
    db.execute(text("select 1"))
    return {"status": "ok", "llm": settings.llm_provider}


if FRONTEND.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/widget.js", include_in_schema=False)
    def widget():
        return FileResponse(FRONTEND / "widget.js", media_type="text/javascript")

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(FRONTEND / "index.html")

    @app.get("/admin", include_in_schema=False)
    def dashboard():
        return FileResponse(FRONTEND / "admin.html")

    @app.get("/demo/{tenant}", include_in_schema=False)
    def demo_site(tenant: str):
        if tenant not in TENANTS:
            raise HTTPException(404)
        return FileResponse(FRONTEND / f"{tenant}.html")
