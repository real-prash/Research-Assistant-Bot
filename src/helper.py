import time
import random
import re
import operator
from typing import List, Annotated
from typing_extensions import TypedDict
from pydantic import BaseModel, Field

# LangChain / LangGraph Imports
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, get_buffer_string
from langchain_community.document_loaders import WikipediaLoader
from langgraph.graph import START, END, StateGraph, MessagesState
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Send 

# Internal Imports (Using relative imports for package compatibility)
from src.config import llm_planner, llm_worker, tavily_search
from src.prompt import (
    analyst_instructions, 
    question_instructions, 
    answer_instructions, 
    section_writer_instructions, 
    report_writer_instructions, 
    intro_conclusion_instructions
)

# --- DATA MODELS ---
class Analyst(BaseModel):
    affiliation: str = Field(description="Primary affiliation of the analyst.")
    name: str = Field(description="Name of the analyst.")
    role: str = Field(description="Role of the analyst in the context of the topic.")
    description: str = Field(description="Description of the analyst focus, concerns, and motives.")
    
    @property
    def persona(self) -> str:
        return f"Name: {self.name}\nRole: {self.role}\nAffiliation: {self.affiliation}\nDescription: {self.description}\n"

class Perspectives(BaseModel):
    analysts: List[Analyst] = Field(description="Comprehensive list of analysts.")

class GenerateAnalystsState(TypedDict):
    topic: str
    max_analysts: int
    human_analyst_feedback: str
    analysts: List[Analyst]

class InterviewState(MessagesState):
    max_num_turns: int
    context: Annotated[list, operator.add]
    analyst: Analyst
    interview: str
    sections: list

class ResearchGraphState(TypedDict):
    topic: str 
    max_analysts: int 
    human_analyst_feedback: str 
    analysts: List[Analyst] 
    sections: Annotated[list, operator.add] 
    introduction: str 
    content: str 
    conclusion: str 
    final_report: str 

# --- NODE FUNCTIONS ---

def create_analysts(state: GenerateAnalystsState):
    topic = state['topic']
    max_analysts = state['max_analysts']
    
    # Get the feedback provided by the user (if any)
    feedback = state.get('human_analyst_feedback', '')
    
    # We add this print statement for debugging to confirm feedback is received
    if feedback:
        print(f"🔄 Regenerating analysts with feedback: {feedback}")

    structured_llm = llm_planner.with_structured_output(Perspectives)
    
    system_msg = analyst_instructions.format(
        topic=topic, 
        human_analyst_feedback=feedback, 
        max_analysts=max_analysts
    )
    
    analysts = structured_llm.invoke([SystemMessage(content=system_msg)]+[HumanMessage(content="Generate the set of analysts.")])
    
    # CRITICAL FIX: We must return the new analysts AND clear the feedback 
    # so the graph doesn't get stuck in a "feedback loop" forever.
    return {
        "analysts": analysts.analysts,
        "human_analyst_feedback": None # Reset feedback after using it
    }

def generate_question(state: InterviewState):
    # Staggering to prevent API limit hits
    time.sleep(random.uniform(2, 4))
    
    analyst = state["analyst"]
    messages = state["messages"]
    system_msg = question_instructions.format(goals=analyst.persona)
    question = llm_worker.invoke([SystemMessage(content=system_msg)]+messages)
    return {"messages": [question]}

def search_web(state: InterviewState):
    """ Search using Direct Prompting """
    messages = state['messages']
    prompt = SystemMessage(content="Generate a concise web search query. Return ONLY the query string.")
    response = llm_worker.invoke(messages + [prompt])
    search_query = response.content.strip('"').strip()
    
    try:
        results = tavily_search.invoke({"query": search_query})
        data = results if isinstance(results, list) else [results]
        formatted = "\n\n---\n\n".join(
            [f'<Document href="{doc.get("url", "")}"/>\n{doc.get("content", "")}\n</Document>' for doc in data]
        )
    except:
        formatted = f"Search failed for: {search_query}"
    return {"context": [formatted]}

def search_wikipedia(state: InterviewState):
    messages = state['messages']
    prompt = SystemMessage(content="Generate a concise Wikipedia search term. Return ONLY the term.")
    response = llm_worker.invoke(messages + [prompt])
    search_query = response.content.strip('"').strip()
    
    try:
        docs = WikipediaLoader(query=search_query, load_max_docs=3).load()
        formatted = "\n\n---\n\n".join(
            [f'<Document source="{d.metadata.get("source", "Wiki")}"/>\n{d.page_content}\n</Document>' for d in docs]
        )
    except:
        formatted = "No wikipedia results."
    return {"context": [formatted]}

def generate_answer(state: InterviewState):
    analyst = state["analyst"]
    messages = state["messages"]
    context = state["context"]
    system_msg = answer_instructions.format(goals=analyst.persona, context=context)
    answer = llm_worker.invoke([SystemMessage(content=system_msg)]+messages)
    answer.name = "expert"
    return {"messages": [answer]}

def save_interview(state: InterviewState):
    return {"interview": get_buffer_string(state["messages"])}

def write_section(state: InterviewState):
    # Heavier staggering for the summarization task
    delay = random.uniform(5, 10)
    print(f"    (Summarizing... Staggering {delay:.1f}s)")
    time.sleep(delay)

    analyst = state["analyst"]
    context = state["context"]
    
    system_msg = section_writer_instructions.format(focus=analyst.description)
    section = llm_worker.invoke([SystemMessage(content=system_msg)]+[HumanMessage(content=f"Use this source: {context}")])
    
    # URL Extraction
    urls = re.findall(r'href="(.*?)"', str(context))
    section_content = section.content
    if urls:
        section_content += "\n\n### Raw Sources\n"
        for url in set(urls):
            section_content += f"- {url}\n"
            
    return {"sections": [section_content]}

def write_report(state: ResearchGraphState):
    sections = state["sections"]
    topic = state["topic"]
    formatted_sections = "\n\n".join([f"{s}" for s in sections])
    system_msg = report_writer_instructions.format(topic=topic, context=formatted_sections)
    report = llm_planner.invoke([SystemMessage(content=system_msg)]+[HumanMessage(content="Write report.")])
    return {"content": report.content}

def write_introduction(state: ResearchGraphState):
    sections = state["sections"]
    topic = state["topic"]
    formatted_sections = "\n\n".join([f"{s}" for s in sections])
    instructions = intro_conclusion_instructions.format(topic=topic, formatted_str_sections=formatted_sections)
    intro = llm_planner.invoke([instructions]+[HumanMessage(content="Write introduction.")])
    return {"introduction": intro.content}

def write_conclusion(state: ResearchGraphState):
    sections = state["sections"]
    topic = state["topic"]
    formatted_sections = "\n\n".join([f"{s}" for s in sections])
    instructions = intro_conclusion_instructions.format(topic=topic, formatted_str_sections=formatted_sections)
    conclusion = llm_planner.invoke([instructions]+[HumanMessage(content="Write conclusion.")])
    return {"conclusion": conclusion.content}

def finalize_report(state: ResearchGraphState):
    content = state["content"].replace("## Insights", "").strip()
    if "## Sources" in content:
        content, sources = content.split("\n## Sources\n")
    else:
        sources = None
    final = f"{state['introduction']}\n\n---\n\n## Insights\n{content}\n\n---\n\n{state['conclusion']}"
    if sources: final += f"\n\n## Sources\n{sources}"
    return {"final_report": final}

# --- EDGE & ROUTING LOGIC ---

def human_feedback(state): pass

def initiate_all_interviews(state):
    if state.get('human_analyst_feedback'): return "create_analysts"
    topic = state["topic"]
    return [Send("conduct_interview", {
        "analyst": analyst,
        "messages": [HumanMessage(content=f"So you said you were writing an article on {topic}?")]
    }) for analyst in state["analysts"]]

def route_messages(state, name="expert"):
    messages = state["messages"]
    if len([m for m in messages if isinstance(m, AIMessage) and m.name == name]) >= state.get('max_num_turns', 2):
        return 'save_interview'
    if "Thank you so much for your help" in messages[-2].content:
        return 'save_interview'
    return "ask_question"

# --- GRAPH COMPILATION ---

def build_graph():
    """Compiles and returns the LangGraph executable"""
    
    # 1. Interview Sub-Graph
    interview_builder = StateGraph(InterviewState)
    interview_builder.add_node("ask_question", generate_question)
    interview_builder.add_node("search_web", search_web)
    interview_builder.add_node("search_wikipedia", search_wikipedia)
    interview_builder.add_node("answer_question", generate_answer)
    interview_builder.add_node("save_interview", save_interview)
    interview_builder.add_node("write_section", write_section)

    interview_builder.add_edge(START, "ask_question")
    interview_builder.add_edge("ask_question", "search_web")
    interview_builder.add_edge("ask_question", "search_wikipedia")
    interview_builder.add_edge("search_web", "answer_question")
    interview_builder.add_edge("search_wikipedia", "answer_question")
    interview_builder.add_conditional_edges("answer_question", route_messages, ['ask_question', 'save_interview'])
    interview_builder.add_edge("save_interview", "write_section")
    interview_builder.add_edge("write_section", END)
    
    interview_graph = interview_builder.compile()

    # 2. Main Graph
    builder = StateGraph(ResearchGraphState)
    builder.add_node("create_analysts", create_analysts)
    builder.add_node("human_feedback", human_feedback)
    builder.add_node("conduct_interview", interview_graph)
    builder.add_node("write_report", write_report)
    builder.add_node("write_introduction", write_introduction)
    builder.add_node("write_conclusion", write_conclusion)
    builder.add_node("finalize_report", finalize_report)

    builder.add_edge(START, "create_analysts")
    builder.add_edge("create_analysts", "human_feedback")
    builder.add_conditional_edges("human_feedback", initiate_all_interviews, ["create_analysts", "conduct_interview"])
    builder.add_edge("conduct_interview", "write_report")
    builder.add_edge("conduct_interview", "write_introduction")
    builder.add_edge("conduct_interview", "write_conclusion")
    builder.add_edge(["write_conclusion", "write_report", "write_introduction"], "finalize_report")
    builder.add_edge("finalize_report", END)

    memory = MemorySaver()
    return builder.compile(interrupt_before=['human_feedback'], checkpointer=memory)

# Export the ready-to-use graph
graph = build_graph()