import os
from lxml import etree
from gpt4all import GPT4All
from langchain_huggingface import HuggingFaceEmbeddings  # updated import
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains import RetrievalQA
from langchain.llms.base import LLM
from langchain_chroma import Chroma  # updated import for Chroma
from typing import Optional, List, Mapping, Any

# --- 1. Функция чтения FB2 ---
def read_fb2(file_path: str) -> str:
    parser = etree.XMLParser(recover=True)
    tree = etree.parse(file_path, parser)
    # ns = {"fb": "http://www.gribuser.ru/xml/fictionbook/2.0"}
    text_nodes = tree.xpath("//text()")
    text_nodes = [node.strip() for node in text_nodes if node.strip() and node.strip() not in ("\n", "\r")]
    return "\n".join(text_nodes)

# --- 2. Обертка GPT4All для LangChain ---
class GPT4AllLLM(LLM):
    def __init__(self, model_path: str):
        self.model = GPT4All(model_path)

    def _call(self, prompt: str, stop: Optional[List[str]] = None) -> str:
        return self.model.generate(prompt)

    @property
    def _identifying_params(self) -> Mapping[str, Any]:
        return {"name": "gpt4all"}

    @property
    def _llm_type(self) -> str:
        return "gpt4all"

# --- 3. Основная функция ---
def summarize_fb2(file_path: str, model_path: str):
    # Читаем книгу
    book_text = read_fb2(file_path)

    # Делим текст на куски
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    chunks = splitter.split_text(book_text)

    # Создаем векторное хранилище
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    vectordb = Chroma(persist_directory="./chroma_fb2", embedding_function=embeddings)
    vectordb.add_texts(chunks)

    # Подключаем GPT4All
    llm = GPT4AllLLM(model_path=model_path)

    # Строим цепочку для QA
    qa = RetrievalQA.from_chain_type(
        llm=llm,
        retriever=vectordb.as_retriever()
    )

    # Запрос на краткий пересказ
    summary = qa.run("Сделай краткий пересказ книги в нескольких абзацах.")
    return summary

# --- 4. Пример запуска ---
if __name__ == "__main__":
    fb2_file = "books/book.fb2.xml"  # путь к книге
    model_file = "./models/gpt4all-7b-chat-q4_0.gguf"  # путь к модели
    result = summarize_fb2(fb2_file, model_file)
    print("\n===== КРАТКИЙ ПЕРЕССКАЗ =====\n")
    print(result)
