# -*- coding: utf-8 -*-
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.prompts import SystemMessagePromptTemplate, HumanMessagePromptTemplate
from langchain_core.runnables import RunnableParallel, RunnableLambda
from langchain_deepseek import ChatDeepSeek

# Install `langchain-deepseek` and set environment variable `DEEPSEEK_API_KEY`.
os.environ["DEEPSEEK_API_KEY"] = os.getenv("DEEPSEEK_API_KEY") or "sk-1973ffcd104c4ca9a118cd7686dbdc1a"

deepseek_llm = ChatDeepSeek(
    model="deepseek-chat",
    temperature=0.0
)

system_prompt = SystemMessagePromptTemplate.from_template(
    """
    ты -- ИИ-помощник для индексации больших книг. твои задачи: 
    - делать краткие конспекты по главам
    - выделять ключевые слова и фразы
    ты должен быть кратким, точным и информативным.
    """)

user_prompt = HumanMessagePromptTemplate.from_template(
    """
Прочитай текст и сделай следующиее:
1. Выдай список всех участвующих персонажей
2. Выдай события в хронологическом порядке в краткой форме

Текст для обработки:
{text}
""",
    input_variables=["text"])

text = """  
Жили-были дед да бабка.

Сидел как-то дед на печи, есть захотел, и говорит бабке:

— Испеки-ка, бабка, колобок!

А бабка ему и отвечает:

— Да ты что, старый! У нас уж, почитай, неделю муки нет!

— А ты, бабка, пойди, по сусекам поскреби, по амбару помети! Авось, наберётся муки-то на колобок!

Вот пошла бабка — по сусекам поскребла, по амбару помела, да наскребла таки муки на колобок! Замесила тесто, истопила печку, испекла колобок. Получился колобок и пышен, и ароматен. Положила бабка колобок на окошко остывать. А колобок прыг за окно — и покатился себе по тропинке, да за околицу. Идёт гуляет, песни распевает, гусей да кур пугает:

— Я по сусекам скребён, по амбару метён, в печку сажён, на окошке стужён! Я от дедушки ушёл и от бабушки ушёл!

Встретился колобку заяц и говорит ему:

— Колобок, колобок, румяный бок! Я тебя съем!

А колобок ему в ответ:

— Я по сусекам скребён, по амбару метён, в печку сажён, на окошке стужён! Я от дедушки ушёл и от бабушки ушёл! А от тебя, заяц, и подавно уйду!

И покатился себе дальше.

Встретился колобку волк и говорит ему:

— Колобок, колобок, румяный бок! Я тебя съем!

А колобок ему в ответ:

— Я по сусекам скребён, по амбару метён, в печку сажён, на окошке стужён! Я от деда ушёл и от бабы ушёл! Я от зайца ушёл, а от тебя, волк, и подавно уйду!

И покатился себе дальше.

Встретился колобку медведь косолапый и говорит ему:

— Колобок, колобок, румяный бок! Я тебя съем!

А колобок ему в ответ:

— Я по сусекам скребён, по амбару метён, в печку сажён, на окошке стужён! Я от дедушки ушёл и от бабушки ушёл! Я от зайца ушёл, от волка ушёл, а от тебя, медведь, и подавно уйду!

И покатился себе дальше.



Встретилась колобку лиса и говорит ему:

— Колобок, колобок, румяный бок! Я тебя съем!

А колобок ей в ответ:

— Я по сусекам скребён, по амбару метён, в печку сажён, на окошке стужён! Я от дедушки ушёл и от бабушки ушёл! Я от зайца ушёл, от волка ушёл, от медведя ушёл, а от тебя, лиса, и подавно уйду!

А лиса и говорит:

— Ах, как славно ты поёшь! Да вот, плохо я слышать стала. Сядь ко мне на нос, да расскажи ещё разок!

Колобок обрадовался, что его послушали, прыгнул лисе на нос и запел:

— Я по сусекам скребён, по амбару метён, в печку сажён, на окошке стужён!..

Но не успел он допеть, как лиса подбросила его носом, пасть раскрыла да и проглотила.
 """

chat_prompt = ChatPromptTemplate.from_messages([system_prompt, user_prompt])

# chain = chat_prompt | deepseek_llm
# result = chain.invoke({"text": text})
# logger.info(f"result.content: ---\n{result.content}")
# logger.info(f"result.response_metadata: {result.response_metadata}")


# ============================================================================
# Fairy Tale Continuation and Merging Pipeline
# ============================================================================

logger.info("\n\n=== Starting Fairy Tale Pipeline ===\n")

# Input texts for two fairy tales
fairy_tale_1_start = """
Жила-была принцесса в высокой башне. Каждый день она смотрела в окно и мечтала о свободе. 
Однажды утром она услышала странный звук - это был дракон, который приземлился на крышу её башни.
"""

fairy_tale_2_start = """
В дремучем лесу жил молодой рыцарь, который потерял свой меч в битве с гоблинами.
Он бродил по лесу в поисках нового оружия, когда наткнулся на говорящего ворона.
"""

# Prompts for continuing fairy tales (reusable for both tales)
continueation_system_prompt = SystemMessagePromptTemplate.from_template(
    """Ты - мастер сказочных историй. Продолжи начало сказки естественным образом, 
    добавив 3-4 абзаца развития сюжета. Будь креативным и интересным."""
)

continuation_user_prompt = HumanMessagePromptTemplate.from_template(
    """Продолжи эту сказку:

{tale_start}

Напиши продолжение (3-4 абзаца):""",
    input_variables=["tale_start"]
)

# Step 3: Merge both tales
merge_system_prompt = SystemMessagePromptTemplate.from_template(
    """Ты - мастер сказочных историй. Тебе даны две разные сказки. 
    Твоя задача - соединить их в одну связную историю, где персонажи из обеих сказок встречаются 
    и их сюжетные линии переплетаются. Создай логичный и интересный финал."""
)

merge_user_prompt = HumanMessagePromptTemplate.from_template(
    """Соедини эти две сказки в одну связную историю:

СКАЗКА 1:
{tale_1}

СКАЗКА 2:
{tale_2}

Напиши объединённую сказку с общим сюжетом и финалом:""",
    input_variables=["tale_1", "tale_2"]
)

# Build unified chain
continuation_prompt_template = ChatPromptTemplate.from_messages([continueation_system_prompt, continuation_user_prompt])
merge_prompt_template = ChatPromptTemplate.from_messages([merge_system_prompt, merge_user_prompt])

# Helper functions for the chain
def continue_tale_1(x):
    logger.info("Step 1: Continuing first fairy tale...")
    chain = continuation_prompt_template | deepseek_llm
    result = chain.invoke({"tale_start": x["tale_1_start"]})
    return x["tale_1_start"] + "\n\n" + result.content

def continue_tale_2(x):
    logger.info("Step 2: Continuing second fairy tale...")
    chain = continuation_prompt_template | deepseek_llm
    result = chain.invoke({"tale_start": x["tale_2_start"]})
    return x["tale_2_start"] + "\n\n" + result.content

def merge_tales(x):
    logger.info("Step 3: Merging both tales...")
    chain = merge_prompt_template | deepseek_llm
    result = chain.invoke({"tale_1": x["tale_1_full"], "tale_2": x["tale_2_full"]})
    return result.content

# Unified pipeline chain - single execution flow
chain_fairy_tales = (
    RunnableParallel(
        tale_1_start=RunnableLambda(lambda x: x["tale_1_start"]),
        tale_2_start=RunnableLambda(lambda x: x["tale_2_start"])
    )
    | RunnableParallel(
        tale_1_full=RunnableLambda(continue_tale_1),
        tale_2_full=RunnableLambda(continue_tale_2),
        tale_1_start=RunnableLambda(lambda x: x["tale_1_start"]),
        tale_2_start=RunnableLambda(lambda x: x["tale_2_start"])
    )
    | RunnableParallel(
        merged_content=RunnableLambda(merge_tales),
        tale_1_full=RunnableLambda(lambda x: x["tale_1_full"]),
        tale_2_full=RunnableLambda(lambda x: x["tale_2_full"])
    )
)

# Execute unified pipeline
logger.info("Executing unified fairy tale pipeline...")
result = chain_fairy_tales.invoke({
    "tale_1_start": fairy_tale_1_start,
    "tale_2_start": fairy_tale_2_start
})

logger.info(f"\nFirst tale continued:\n{result['tale_1_full']}\n")
logger.info(f"\nSecond tale continued:\n{result['tale_2_full']}\n")
logger.info(f"\n{'='*80}")
logger.info("MERGED FAIRY TALE:")
logger.info(f"{'='*80}\n")
logger.info(result['merged_content'])
logger.info(f"\n{'='*80}\n")

# Save results to files
output_dir = "fairy_tales_output"
os.makedirs(output_dir, exist_ok=True)

with open(f"{output_dir}/tale_1_full.txt", "w", encoding="utf-8") as f:
    f.write(result['tale_1_full'])

with open(f"{output_dir}/tale_2_full.txt", "w", encoding="utf-8") as f:
    f.write(result['tale_2_full'])

with open(f"{output_dir}/tale_merged.txt", "w", encoding="utf-8") as f:
    f.write(result['merged_content'])

logger.info(f"Results saved to '{output_dir}/' directory")
logger.info("=== Pipeline Complete ===")
