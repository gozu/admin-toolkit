# DSS API Reference Notes

This directory is the local Admin Toolkit copy of DSS API-reference findings.
It is not a vendored copy of the full generated API reference.

Current source of truth for DSS 14.7.x:

- Python client API reference:
  https://developer.dataiku.com/latest/api-reference/python/index.html#gsc.tab=0
- DSS REST API reference for DSS 14:
  https://doc.dataiku.com/dss/api/14/rest/

## Local Files

| File | API-reference role | Status |
| --- | --- | --- |
| `dss-api-testing.md` | Quick patterns for probing DSS with the Python client and public REST paths. | Refreshed for DSS 14.7.x source URLs and REST 14 path spellings. |
| `containerized-execution-object-scan.md` | Container-execution object field map, mixing public REST endpoints with Python API handles where DSS 14 does not publish a REST endpoint. | Refreshed for DSS 14.7.x API-reference split. |
| `macro-impersonation.md` | Plugin runnable behavior note. | Not a generated DSS API reference copy. |
| `schemamigration.md` | Admin Toolkit implementation plan. | Not a generated DSS API reference copy. |

## DSS 14.7.x Python API Areas To Remember

The Python API index includes DSS 14 agentic and admin surfaces that older local
notes can miss:

- Agents and agent tools: `DSSAgent`, `DSSAgentSettings`,
  `DSSAgentVersionSettings`, `DSSAgentInteractionLoggingSettings`,
  `DSSAgentTool`, `DSSAgentToolCreator`, `DSSAgentToolSettings`,
  `DSSVectorStoreSearchAgentToolCreator`, and
  `DSSVectorStoreSearchAgentToolSettings`.
- Project methods for agentic/RAG work:
  `list_knowledge_banks`, `get_knowledge_bank`, `create_knowledge_bank`,
  `create_retrieval_augmented_llm`, `list_agents`, `get_agent`,
  `create_agent`, `create_agent_interaction_logging_dataset`,
  `new_agent_tool`, `list_agent_tools`, `get_agent_tool`,
  `new_cobuild_conversation`, `list_agent_reviews`, `get_agent_review`, and
  `create_agent_review`.
- Admin/reference areas now present in the Python API index:
  Messaging channels, Project Standards, Enterprise Asset Library, Agents
  Review, Code Studios, Data Collections, Data Quality, and Webapps.

Use the REST 14 reference for public endpoint spelling and privilege semantics.
Use the Python API reference for handles such as project settings, Code Studios,
agent tools, and agent reviews when the REST 14 page does not publish a direct
endpoint.
