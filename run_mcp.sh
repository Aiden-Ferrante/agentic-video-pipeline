#!/usr/bin/env bash
# stdio entrypoint for the video-pipeline MCP server.
cd "$(dirname "$0")"
exec .venv/bin/python -m pipeline.mcp_server
