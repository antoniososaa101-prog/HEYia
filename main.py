# ============================
# 🚀 HEYiA FINAL SaaS
# ============================

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from passlib.context import CryptContext
from jose import jwt
from pydantic import BaseModel, EmailStr
import stripe, os, redis, httpx, secrets

# ============================
# ⚙️ CONFIG
# ============================

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
STRIPE_SECRET = os.getenv("STRIPE_SECRET")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

stripe.api_key = STRIPE_SECRET

# ============================
# 🧠 APP
# ============================

app = FastAPI(title="HEYiA SaaS FINAL")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================
# 🧠 DB
# ============================

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ============================
# 👤 MODELO
# ============================

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True)
    password = Column(String)
    plan = Column(String, default="free")
    affiliate_code = Column(String, unique=True)
    referred_by = Column(String)
    earnings = Column(Float, default=0.0)

Base.metadata.create_all(bind=engine)

# ============================
# 🔐 SEGURIDAD
# ============================

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2 = OAuth2PasswordBearer(tokenUrl="login")

def hash_pass(p): return pwd.hash(p)
def verify_pass(p, h): return pwd.verify(p, h)
def create_token(data): return jwt.encode(data, SECRET_KEY, algorithm="HS256")

def get_user(token: str = Depends(oauth2), db: Session = Depends(get_db)):
    payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    return db.query(User).filter(User.id == payload["id"]).first()

# ============================
# ⚡ REDIS
# ============================

r = redis.Redis(host="localhost", port=6379)

def rate_limit(uid):
    key = f"user:{uid}"
    if r.get(key) and int(r.get(key)) > 100:
        raise HTTPException(429)
    r.incr(key)
    r.expire(key, 60)

# ============================
# 📩 MODELOS
# ============================

class Register(BaseModel):
    email: EmailStr
    password: str
    referral: str = None

class Login(BaseModel):
    email: EmailStr
    password: str

class Chat(BaseModel):
    message: str

# ============================
# 👤 REGISTRO (AFILIADOS)
# ============================

@app.post("/register")
def register(data: Register, db: Session = Depends(get_db)):

    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(400, "Existe")

    code = secrets.token_hex(4)

    user = User(
        email=data.email,
        password=hash_pass(data.password),
        affiliate_code=code,
        referred_by=data.referral
    )

    db.add(user)
    db.commit()

    return {"msg": "ok", "affiliate_code": code}

# ============================
# 🔑 LOGIN
# ============================

@app.post("/login")
def login(data: Login, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()

    if not user or not verify_pass(data.password, user.password):
        raise HTTPException(401)

    return {"access_token": create_token({"id": user.id})}

# ============================
# 💳 STRIPE
# ============================

PRICE_ID = "price_REAL_AQUI"

@app.post("/checkout")
def checkout(user=Depends(get_user)):

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="subscription",
        line_items=[{"price": PRICE_ID, "quantity": 1}],
        client_reference_id=str(user.id),
        success_url="https://heyia-production.up.railway.app/success",
        cancel_url="https://heyia-production.up.railway.app/cancel"
    )

    return {"url": session.url}

# ============================
# 🔥 WEBHOOK ROBUSTO
# ============================

@app.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig, STRIPE_WEBHOOK_SECRET
        )
    except:
        raise HTTPException(400, "Webhook inválido")

    if event["type"] == "checkout.session.completed":
        try:
            session = event["data"]["object"]

            user_id = session.get("client_reference_id")
            user = db.query(User).filter(User.id == int(user_id)).first()

            if user:
                user.plan = "pro"

                # 💰 comisión dinámica (20%)
                if user.referred_by:
                    ref = db.query(User).filter(User.affiliate_code == user.referred_by).first()
                    if ref:
                        ref.earnings += 0.2 * 70  # ejemplo 20% plan base
                db.commit()

        except Exception as e:
            print("Error webhook:", e)

    return {"ok": True}

# ============================
# 🤖 IA
# ============================

async def ask_ai(prompt):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "messages":[
                    {"role":"system","content":"Eres HEYiA, un amigo experto útil."},
                    {"role":"user","content":prompt}
                ]
            }
        )
    return r.json()["choices"][0]["message"]["content"]

# ============================
# 🚀 CHAT
# ============================

@app.post("/chat")
async def chat(data: Chat, user=Depends(get_user)):
   # rate_limit(user.id)

    if user.plan == "free":
        raise HTTPException(403, "Upgrade requerido")

    res = await ask_ai(data.message)
    return {"respuesta": res}

# ============================
# 📊 DASHBOARD PRO (GRÁFICAS)
# ============================

@app.get("/admin", response_class=HTMLResponse)
def admin(db: Session = Depends(get_db)):

    users = db.query(User).all()
    total = len(users)
    pro = len([u for u in users if u.plan=="pro"])
    earnings = sum([u.earnings for u in users])

    return f"""
    <html>
    <head>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body style="background:#0f172a;color:white;padding:20px">

    <h1>HEYiA Dashboard</h1>
    <p>Total: {total}</p>
    <p>Pro: {pro}</p>
    <p>Comisiones: ${earnings}</p>

    <canvas id="chart"></canvas>

    <script>
    new Chart(document.getElementById('chart'), {{
        type: 'bar',
        data: {{
            labels: ['Usuarios','Pro'],
            datasets: [{{
                label: 'Datos',
                data: [{total},{pro}]
            }}]
        }}
    }});
    </script>

    </body>
    </html>
    """

# ============================
# 🌐 LANDING PAGE
# ============================

@app.get("/", response_class=HTMLResponse)
def landing():
    return """
    <html>
    <body style="background:#0f172a;color:white;text-align:center;padding:50px">

    <h1>HEYiA</h1>
    <p>Tu IA que sí te entiende</p>

    <a href="/registro?ref=">
    <button style="padding:15px;font-size:20px">Empezar</button>
    </a>

    </body>
    </html>
    """
