from pydantic import BaseModel, Field
from typing_extensions import TypedDict 
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.messages import HumanMessage, SystemMessage 
from abc import ABC, abstractmethod

import os
import json

import model
import utils
import tool
from logger import init_logger

class NodeConfig(BaseModel):
    name: str = Field(
        description="The name of the node, used for logging and debugging."
    )

    node_intro_message: str = Field(
        default="<><><><><> 👑 SUPERVISOR 👑 <><><><><>"
        description="The introduction message for the node."
    )

    log_filename: str = Field(
        description="The filename for the log file."
    )

    config_filename: str = Field(
        description="The filename for the config file."
    )

    transition_objs: dict = Field(
        default_factory=dict,
        description="A dictionary of transition objects for the node."
    )
    
    system_prompt_key: str = Field(
        default="supervisor_system_prompt_filename",
        description="The key for the system prompt in the config file."
    )

    default_system_prompt_filename: str = Field(
        default="prompts/exp-supervisor.txt",
        description="The default filename for the system prompt."
    )

class BaseNode(ABC):
    def __init__(self, config: NodeConfig, State, store, metadata_store, memory, tools: list):
        self.node_config = config
        self.curie_logger = init_logger(self.node_config.log_filename)
        self.State = State
        self.store = store
        self.metadata_store = metadata_store
        self.memory = memory
        self.tools = tools

    def create_subgraph(self):
        """ Creates a Node subgraph."""

        subgraph_builder = StateGraph(State)
        
        with open(self.node_config.config_filename, 'r') as file:
            config = json.load(file)
    
        system_prompt_file = config.get(self.node_config.system_prompt_key, self.node_config.default_system_prompt_filename)

        subgraph_node = self._create_model_response(system_prompt_file) 

        subgraph_builder.add_node(self.node_config.name, subgraph_node)
        subgraph_builder.add_edge(START, self.node_config.name)
        tool_node = ToolNode(tools=self.tools)
        subgraph_builder.add_node("tools", tool_node)

        subgraph_builder.add_conditional_edges(self.node_config.name, tools_condition)
        # supervisor_builder.add_conditional_edges( "tools", router, ["supervisor", END])
        subgraph_builder.add_edge("tools", self.node_config.name)

        subgraph = subgraph_builder.compile(checkpointer=self.memory)
        os.makedirs("../../logs/misc") if not os.path.exists("../../logs/misc") else None
        utils.save_langgraph_graph(subgraph, f"../../logs/misc/{self.node_config.name}_graph_image.png") 

        def call_subgraph(state: self.State) -> self.State: 
            response = subgraph.invoke({
                    "messages": state["messages"][-1]
                },
                {
                    # "recursion_limit": 20,
                    "configurable": {
                        "thread_id": f"{self.node_config.name}_graph_id"
                    }
                }
            )
            
            return {
                "messages": [
                    HumanMessage(content=response["messages"][-1].content, name=f"{self.node_config.name}_graph")
                ],
                "prev_agent": response["prev_agent"],
                "remaining_steps_display": state["remaining_steps"],
            }
        return call_subgraph

    def _create_model_response(self, system_prompt_file):    
        # FIXME: better way to get model names; from config?
        # FIXME: can move model name to model.py 
        def Node(state: self.State):
            if state["remaining_steps"] <= 4:
                return {
                    "messages": [], 
                    "prev_agent": "supervisor",
                }
                
            # Read from prompt file:
            with open(system_prompt_file, "r") as file:
                system_prompt = file.read()

            system_message = SystemMessage(
                content=system_prompt,
            )

            # Query model and append response to chat history 
            messages = state["messages"]

            # Ensure the system prompt is included at the start of the conversation
            if not any(isinstance(msg, SystemMessage) for msg in messages):
                messages.insert(0, system_message)
            
            response = model.query_model_safe(messages, tools=self.tools)
            self.curie_logger.info(self.node_config.node_intro_message)
            self.curie_logger.debug(response)
            if response.tool_calls:
                self.curie_logger.info(f"Tool calls: {response.tool_calls[0]['name']}")

            concise_msg = response.content.split('\n\n')[0]
            if concise_msg:
                self.curie_logger.info(f'Concise message: {concise_msg}')

            return {"messages": [response], "prev_agent": self.node_config.name}
        
        return Node

    @abstractmethod
    def handle_func(self):
        """Handles transition logic and determines next action to take."""
        pass