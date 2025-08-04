# from langchain_community.document_loaders import PyPDFLoader
from langchain.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import OpenAI
import os
import chromadb
from langchain_core.documents import Document
from dotenv import load_dotenv

load_dotenv()

ALIYUN_API_KEY = os.getenv("ALIYUN_API_KEY")
ALIYUN_BASE_URL = os.getenv("ALIYUN_BASE_URL")
RAG_DB_PATH = os.getenv("RAG_DB_PATH")

client = OpenAI(
    api_key=ALIYUN_API_KEY,
    base_url=ALIYUN_BASE_URL
)


# 初始化 ChromaDB 客户端
chromadb_client = chromadb.PersistentClient(path=RAG_DB_PATH)
rag_collections = {
    "运维助手": chromadb_client.get_or_create_collection(name="devops_tool"),
    "产品手册": chromadb_client.get_or_create_collection(name="product_manual")
}

def load_pdf(file_path: str) -> list[str]:
    loader = PyPDFLoader(file_path)
    docs = loader.load()
    for doc in docs:
        doc.page_content = doc.page_content.replace('\n', ' ')
        # print(f"加载啊啊啊啊啊啊啊啊啊啊啊啊啊{doc}")
    return docs

def split_documents(docs: list[any]) -> list[any]:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        add_start_index=True
    )
    all_splits = text_splitter.split_documents(docs)
    return all_splits

def embed(text: str) -> list[float]:
    embedding = client.embeddings.create(
        model="text-embedding-v4",
        input=text,
        dimensions=1024,
        encoding_format="float"
    )
    return embedding.data[0].embedding

def save_to_chroma(splits: list[Document], collection_name: str):
    collection = rag_collections.get(collection_name)
    if not collection:
        return
    
    for i, split in enumerate(splits):
        vector = embed(split.page_content)
        print(f"split=========={split}")
        collection.add(
            ids=str(i),
            documents=[split.page_content],
            embeddings=[vector],
            metadatas=[split.metadata]
        )

def query_chroma(query: str, collection_name: str, n_results: int = 3) -> list[str]:
    collection = rag_collections.get(collection_name)
    if not collection:
        return []
    
    query_vector = embed(query)
    results = collection.query(
        query_embeddings=[query_vector],
        n_results=n_results,
    )
    return results['documents'][0] if results else []

if __name__ == "__main__":
    # devops_file = ""
    product_manual_path = "C:/Users/lzfdd/Desktop/备份软件缺陷管理.pdf"
    docs = load_pdf(product_manual_path)
    splits = split_documents(docs)
    save_to_chroma(splits, "运维助手")
    print("数据已保存到 ChromaDB")
    print(f"集合记录数: {rag_collections['运维助手'].count()}")