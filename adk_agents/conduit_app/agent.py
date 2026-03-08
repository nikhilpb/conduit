"""ADK Web entrypoint for Conduit."""

from conduit.agent import build_root_agent
from conduit.config import get_settings


root_agent = build_root_agent(get_settings())

