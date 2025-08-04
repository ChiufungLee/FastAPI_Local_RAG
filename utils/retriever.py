import chromadb
from typing import List, Dict, Any
from openai import OpenAI  
from langchain_core.documents import Document
from dotenv import load_dotenv
import os

load_dotenv()
ALIYUN_API_KEY = os.getenv("ALIYUN_API_KEY")
ALIYUN_BASE_URL = os.getenv("ALIYUN_BASE_URL")

class ChromaRetriever:
    def __init__(
        self,
        collection_name: str,
        chroma_client: chromadb.Client,
        # ALIYUN_API_KEY: str,
        model_name: str = "text-embedding-v4",
        embedding_dimensions: int = 1024,
        encoding_format: str = "float"
    ):
        """
        初始化 Chroma 检索器
        :param collection_name: Chroma 数据库集合名称
        :param chroma_client: 已初始化的 Chroma 客户端
        :param openai_api_key: OpenAI API 密钥
        :param model_name: 使用的嵌入模型名称
        :param embedding_dimensions: 向量维度（仅支持 text-embedding-v3/v4）
        :param encoding_format: 向量编码格式（float 或 base64）
        """
        self.collection_name = collection_name
        self.chroma_client = chroma_client
        self.model_name = model_name
        self.embedding_dimensions = embedding_dimensions
        self.encoding_format = encoding_format

        # 初始化 OpenAI 客户端
        # self.openai_client = OpenAI(api_key=openai_api_key)
        self.openai_client = OpenAI(
            api_key=ALIYUN_API_KEY, 
            base_url=ALIYUN_BASE_URL
        )

        # 获取 Chroma 集合
        self.collection = self.chroma_client.get_collection(name=collection_name)

    def embed(self, text: str) -> List[float]:
        """
        生成文本的嵌入向量
        :param text: 输入文本
        :return: 嵌入向量
        """
        response = self.openai_client.embeddings.create(
            model=self.model_name,
            input=text,
            dimensions=self.embedding_dimensions,
            encoding_format=self.encoding_format
        )
        print(f"使用的 token 数量为：{response.usage.total_tokens}")
        return response.data[0].embedding  # 返回向量数据

    def get_relevant_documents(self, query: str, n_results: int = 3) -> List[Document]:
        """LangChain标准接口方法"""
        query_vector = self.embed(query)
        results = self.collection.query(
            query_embeddings=[query_vector],
            n_results=n_results,
            include=["documents", "metadatas"]
        )
        
        # 将结果转换为LangChain Document对象
        documents = []
        if results.get('documents'):
            for doc_list in results['documents']:
                for i, text in enumerate(doc_list):
                    metadata = results['metadatas'][0][i] if results.get('metadatas') else {}
                    documents.append(Document(page_content=text, metadata=metadata))
        return documents
    
    def query(
        self,
        query_text: str,
        n_results: int = 3,
        **kwargs
    ) -> Dict[str, Any]:
        """
        执行向量检索
        :param query_text: 查询文本
        :param n_results: 返回结果数量
        :param kwargs: 其他查询参数（传递给 chroma 的 query 方法）
        :return: 查询结果（包含文档和元数据）
        """
        query_vector = self.embed(query_text)
        results = self.collection.query(
            query_embeddings=query_vector,
            n_results=n_results,
            **kwargs
        )
        return results