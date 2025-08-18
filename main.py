import asyncio
import csv
import io
import json
import re
import chromadb
from fastapi import FastAPI, HTTPException, Depends, Query, Request, Form, Response, status
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, desc
from sqlalchemy.orm import sessionmaker, Session, relationship, declarative_base
from sqlalchemy.sql import func
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware
from datetime import datetime, timedelta
import secrets
import uuid
import os
from prompts.prompts import get_prompt
from dotenv import load_dotenv
from utils.retriever import ChromaRetriever

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key=secrets.token_urlsafe(32))

# 挂载静态文件和模板
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

load_dotenv()

deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
RAG_DB_PATH = os.getenv("RAG_DB_PATH")

# SQLite 数据库配置
SQLALCHEMY_DATABASE_URL = "sqlite:///./fast_test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 声明 ORM 基础类
Base = declarative_base()

# 数据库模型
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String) 
    conversations = relationship("Conversation", back_populates="user")

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String, default="新对话")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    scenario = Column(String, default="需求挖掘")
    messages = relationship("Message", back_populates="conversation", order_by="Message.timestamp")
    user = relationship("User", back_populates="conversations")

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String, ForeignKey("conversations.id"))
    role = Column(String)  # "user" or "assistant"
    content = Column(String)
    timestamp = Column(DateTime, default=func.now())
    conversation = relationship("Conversation", back_populates="messages")


# 创建数据库表
Base.metadata.create_all(bind=engine)

# 数据库
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 注册页面
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
async def register_user(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_db)
    ):

    db_user = db.query(User).filter(User.username == username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    new_user = User(username=username, password=password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    return response

# 用户登录
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/", response_class=HTMLResponse)
async def main_page(request: Request):
    username = request.session.get("username")
    if username is None:
        return templates.TemplateResponse("login.html",{"request": request, "error": "用户会话已失效，请重新登录"})
    return RedirectResponse(url="/chat", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/login")
async def login_user(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_db)
):

    db_user = db.query(User).filter(User.username == username).first()
    
    if not db_user or db_user.password != password:
        # raise HTTPException(status_code=401, detail="Invalid credentials",)
        return templates.TemplateResponse(
            "login.html", 
            {"request": request, "error": "用户名或密码错误"},
            status_code=status.HTTP_401_UNAUTHORIZED
        )
    
    request.session["user_id"] = db_user.id
    request.session["username"] = db_user.username
    request.session["login_time"] = datetime.now().isoformat()

    # return {"message": "Login successful", "user_id": db_user.id}
    response = RedirectResponse(url="/chat", status_code=status.HTTP_303_SEE_OTHER)
    return response

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    username = request.session.get("username")
    if username is None:
        return templates.TemplateResponse("login.html",{"request": request, "error": "用户会话已失效，请重新登录"})
    return templates.TemplateResponse("chat.html",{"request": request, "username": username})

@app.get("/logout", response_class=HTMLResponse)
async def logout(request: Request):
    request.session.clear()
    # return templates.TemplateResponse("login.html",{"request": request, })
    return RedirectResponse(url="/login?logout=true", status_code=status.HTTP_303_SEE_OTHER)


# 获取历史记录
@app.get("/api/history")
async def get_history(request: Request, scenario: str, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    
    today = datetime.now().date()
    fewdays_ago = today - timedelta(days=3)
    one_week_ago = today - timedelta(days=7)
    
    conversations = db.query(Conversation).filter(
        Conversation.user_id == user_id,
        Conversation.scenario == scenario
    ).order_by(desc(Conversation.updated_at)).all()
    
    # 按时间分组
    groups = []
    today_group = {"time_group": "当天", "conversations": []}
    fewdays_group = {"time_group": "3天前", "conversations": []}
    week_group = {"time_group": "最近7天", "conversations": []}
    older_group = {"time_group": "更早", "conversations": []}
    
    for conv in conversations:
        conv_date = conv.updated_at.date()
        conv_data = {
            "id": conv.id,
            "title": conv.title,
            "updated_at": conv.updated_at.isoformat()
        }
        
        if conv_date == today:
            today_group["conversations"].append(conv_data)
        elif conv_date >= fewdays_ago:
            fewdays_group["conversations"].append(conv_data)
        elif conv_date >= one_week_ago:
            week_group["conversations"].append(conv_data)
        else:
            older_group["conversations"].append(conv_data)
    
    # 只添加有对话的分组
    if today_group["conversations"]:
        groups.append(today_group)
    if fewdays_group["conversations"]:
        groups.append(fewdays_group)
    if week_group["conversations"]:
        groups.append(week_group)
    if older_group["conversations"]:
        groups.append(older_group)
    return {"groups": groups}

# 获取对话内容
@app.get("/api/conversation/{conversation_id}")
async def get_conversation(
    request: Request, 
    conversation_id: str, 
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    
    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id
    ).first()
    
    if not conversation:
        return JSONResponse(status_code=404, content={"error": "对话不存在"})
    
    messages = [
        {"role": msg.role, "content": msg.content, "timestamp": msg.timestamp.isoformat()}
        for msg in conversation.messages
    ]
    
    return {
        "id": conversation.id,
        "title": conversation.title,
        "scenario": conversation.scenario,
        "messages": messages
    }


# 创建新对话
@app.post("/api/conversation/new")
async def create_new_conversation(
    request: Request,
    scenario: str = Form(...),
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    
    new_conversation = Conversation(
        user_id=user_id,
        title=f"新对话-{datetime.now().strftime('%H:%M')}",
        scenario=scenario
    )
    db.add(new_conversation)
    db.commit()
    db.refresh(new_conversation)
    
    welcome_message = Message(
        conversation_id=new_conversation.id,
        role="assistant",
        content="你好！我是智能助手，有什么可以帮您的吗？"
    )
    db.add(welcome_message)
    db.commit()
    
    return {
        "conversation_id": new_conversation.id,
        "title": new_conversation.title
    }


# 聊天处理
@app.post("/api/chat")
async def chat_endpoint(
    request: Request,
    data: dict,  # 从JSON body中获取数据
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    
    message = data.get("message")
    scenario = data.get("scenario")
    conversation_id = data.get("conversation_id")
    
    # 如果没有对话ID，创建新对话
    new_conversation = None
    if not conversation_id:
        new_conversation = Conversation(
            user_id=user_id,
            title="新对话",
            scenario=scenario
        )
        db.add(new_conversation)
        db.commit()
        db.refresh(new_conversation)
        conversation_id = new_conversation.id
    
    user_message = Message(
        conversation_id=conversation_id,
        role="user",
        content=message
    )
    db.add(user_message)
    db.commit()
    
    # 获取对话历史
    history = get_conversation_history(conversation_id, db)
    
    context = ""
    # 对于需要RAG的场景，获取上下文
    if scenario in ["运维助手", "产品手册"]:
        retriever = get_rag_retriever(scenario)
        if retriever:
            docs = retriever.get_relevant_documents(message)
            context = "\n\n".join([doc.page_content for doc in docs])
            # print(f"检索到的内容是：{context}")
    
    # 生成对话提示
    prompt = get_prompt(
        scenario,
        context=context,
        history=history,
        question=message
    )
    # print(f"当前prompt是{prompt}")
    # 调用大模型
    async def generate_response():
        ai_response = ""
        full_response_saved = False
        
        try:
            words = call_llm_model(prompt)
            for token in words:
                # 检查客户端是否断开连接
                if await request.is_disconnected():
                    print("客户端已断开连接")
                    break
                    
                ai_response += token
                yield f"data: {json.dumps({'token': token})}\n\n"
                await asyncio.sleep(0.02)
        except GeneratorExit:
            # 处理客户端断开连接
            print("流式响应被中断")
        finally:
            # 保存响应
            if ai_response and not full_response_saved:
                save_ai_response(ai_response, conversation_id, db)
                full_response_saved = True

        if not full_response_saved:
            save_ai_response(ai_response, conversation_id, db)
            full_response_saved = True

            yield f"data: {json.dumps({'full_response': ai_response, 'conversation_id': conversation_id})}\n\n"
            
            if new_conversation:
                title_prompt = get_prompt(
                    "标题生成",
                    question=message
                )

                title_str = ''.join(call_llm_model(title_prompt))
                title = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5\s]', '', title_str).strip()
                
                if len(title) > 10:
                    title = title[:10] + "..."

                new_conversation.title = title
                db.add(new_conversation)
                db.commit()
                db.refresh(new_conversation) 
                yield f"data: {json.dumps({'new_conversation_id': conversation_id, 'conversation_title': new_conversation.title})}\n\n"
            
            yield "data: [DONE]\n\n"
    # 返回流式响应
    return StreamingResponse(generate_response(), media_type="text/event-stream")

def save_ai_response(content, conversation_id, db):
    """保存AI响应到数据库"""
    if content:
        ai_message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content
        )
        db.add(ai_message)
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"保存消息失败: {e}")
        
        # 更新对话时间
        conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if conversation:
            conversation.updated_at = func.now()
            try:
                db.commit()
            except:
                db.rollback()

# 删除对话
@app.delete("/api/conversation/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    
    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id
    ).first()
    
    if not conversation:
        return JSONResponse(status_code=404, content={"error": "对话未找到"})
    
    try:
        db.query(Message).filter(Message.conversation_id == conversation_id).delete()
        
        db.delete(conversation)
        db.commit()
        
        return JSONResponse(content={"message": "对话删除成功"})
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    
# 重命名对话
@app.post("/api/conversation/{conversation_id}/rename")
async def rename_conversation(
    conversation_id: str,
    request: Request,
    data: dict,
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    
    new_title = data.get("title", "").strip()
    if not new_title:
        return JSONResponse(status_code=400, content={"error": "标题不能为空"})
    
    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id
    ).first()
    
    if not conversation:
        return JSONResponse(status_code=404, content={"error": "对话未找到"})
    
    conversation.title = new_title
    db.commit()
    
    return JSONResponse(content={"message": "对话重命名成功"})

@app.get("/api/export/testcases")
async def export_testcases(
    conversation_id: str,
    db: Session = Depends(get_db)
):
    # 获取对话中的AI消息
    ai_messages = db.query(Message).filter(
        Message.conversation_id == conversation_id,
        Message.role == "assistant"
    ).order_by(Message.timestamp.desc()).all()
    
    if not ai_messages:
        return JSONResponse(status_code=404, content={"error": "未找到测试用例"})
    
    # 提取最新AI消息中的表格
    latest_ai_message = ai_messages[0].content
    table_data = extract_table_from_markdown(latest_ai_message)
    
    if not table_data:
        return JSONResponse(status_code=404, content={"error": "未找到表格数据"})
    
    # 转换为CSV格式
    csv_data = convert_table_to_csv(table_data)
    
    # 创建响应
    headers = {
        "Content-Disposition": f"attachment; filename=testcases_{conversation_id}.csv",
        "Content-Type": "text/csv"
    }
    return Response(content=csv_data, headers=headers)


# 获取对话历史
def get_conversation_history(conversation_id: str, db: Session) -> str:
    """获取对话的历史消息"""
    messages = db.query(Message).filter(
        Message.conversation_id == conversation_id
    ).order_by(Message.timestamp.asc()).limit(7).all()
    messages = messages[:-1]
    history = []
    for msg in messages:
        role = "用户" if msg.role == "user" else "助手"
        history.append(f"{role}: {msg.content}")
    # print(f"获取的历史消息是：{history}")

    # summary_prompt = get_prompt(scenario="历史摘要", history=history)
    # history_str = "\n".join(history)
    # summary_prompt = get_prompt(scenario="历史摘要", history=history_str)
    # summary_history = ''.join(call_llm_model(summary_prompt))
    # print(f"获取的历史摘要是：{summary_history}")
    # return summary_history
    return "\n".join(history)

def get_rag_retriever(scenario: str):
    """根据场景获取对应的RAG检索器"""

    collection_map = {
        "运维助手": "devops_tool",
        "产品手册": "product_manual"
    }
    
    if scenario not in collection_map:
        return None
        
    collection_name = collection_map[scenario]
    print(f"集合名称是：{collection_name}")
    try:
        # 初始化Chroma客户端
        chroma_client = chromadb.PersistentClient(path=RAG_DB_PATH)
        
        # 创建并返回检索器实例
        return ChromaRetriever(
            collection_name=collection_name,
            chroma_client=chroma_client,
            model_name="text-embedding-v4"
        )
    except Exception as e:
        print(f"创建检索器失败: {e}")
        return None

def call_llm_model(prompt):

    from langchain.chat_models import init_chat_model
    model = init_chat_model(
        model="deepseek-chat", 
        model_provider="deepseek",
        api_key = deepseek_api_key,
        temperature=0.7)
    # print(f"当前 prompt 是：{prompt}")

    for token in model.stream(prompt):
        yield token.content
    # response = model.invoke(prompt)
    # print(f"LLM response: {response}")
    # return response.content

# 辅助函数：从Markdown中提取表格
def extract_table_from_markdown(text: str) -> list:
    """
    从Markdown文本中提取表格数据
    
    返回:
        list: 二维列表，包含表格行和列数据
    """
    table_data = []
    in_table = False
    
    for line in text.split('\n'):
        line = line.strip()
        # 检测表格开始
        if line.startswith('|') and ('---' in line or '--' in line):
            in_table = True
            continue
        
        # 处理表格行
        if in_table and line.startswith('|'):
            # 移除首尾的|并分割单元格
            cells = [cell.strip() for cell in line.split('|')[1:-1]]
            table_data.append(cells)
        elif in_table and not line.startswith('|'):
            # 表格结束
            break
    
    return table_data

# 辅助函数：将表格数据转换为CSV
def convert_table_to_csv(table_data: list) -> str:
    """
    将表格数据转换为CSV格式字符串
    
    参数:
        table_data: 二维表格数据
        
    返回:
        str: CSV格式的字符串
    """
    if not table_data:
        return ""
    
    # 创建CSV内容
    output = io.StringIO()
    writer = csv.writer(output)
    
    # 写入表头
    writer.writerow(table_data[0])
    
    # 写入数据行
    for row in table_data[1:]:
        writer.writerow(row)
    
    return output.getvalue()