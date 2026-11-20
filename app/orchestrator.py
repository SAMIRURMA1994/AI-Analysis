import json
import re
from typing import Any

from .ollama import OllamaClient, parse_json_object
from .config import settings

SYSTEM_PROMPT = """You are a helpful GitHub, browser-automation, local-search, and MySQL assistant.
You have access to the numbered MCP tools listed below. Decide whether the user's request needs a tool.
Return ONLY valid JSON in one of these forms:
{"type":"tool_call","tool_index":0,"arguments":{}}
{"type":"final","answer":"natural language answer"}
For a tool request, select the exact numeric tool_index from the catalog.
Do not output a tool name. Never invent a tool index or arguments.
Never invent database column values. Use only values returned by a database tool.
"""


class Assistant:
    def __init__(self, github: Any, ollama: OllamaClient) -> None:
        self.github = github
        self.ollama = ollama

    @staticmethod
    def _debug_value(value: Any, limit: int = 2000) -> str:
        """Format trace output without printing credentials or huge results."""
        if isinstance(value, dict):
            safe = {
                key: "<redacted>"
                if any(secret in key.lower() for secret in ("token", "password", "secret", "credential"))
                else item
                for key, item in value.items()
            }
            value = safe
        text = json.dumps(value, default=str, ensure_ascii=False)
        return text[:limit] + ("..." if len(text) > limit else "")

    @staticmethod
    def _improve_request(question: str) -> str:
        """Normalize user wording before intent and MCP-server routing."""
        improved = re.sub(r"\s+", " ", question.strip())
        corrections = {
            "opnen": "open",
            "navgate": "navigate",
            "playwrites": "playwright",
            "repositorie": "repository",
            "repositoris": "repositories",
        }
        for incorrect, corrected in corrections.items():
            improved = re.sub(
                rf"\b{re.escape(incorrect)}\b",
                corrected,
                improved,
                flags=re.IGNORECASE,
            )
        return improved

    @staticmethod
    def _normalize_decision(decision: dict[str, Any]) -> dict[str, Any]:
        """Accept common JSON variants produced by small local models."""
        if decision.get("type") == "tool_call":
            if "tool_index" not in decision and "index" in decision:
                decision["tool_index"] = decision["index"]
            return decision
        if "tool_index" in decision or "index" in decision:
            return {
                "type": "tool_call",
                "tool_index": decision.get("tool_index", decision.get("index")),
                "arguments": decision.get("arguments", decision.get("parameters", {})),
            }
        if decision.get("type") == "final":
            return decision

        answer = decision.get("answer") or decision.get("response")
        if isinstance(answer, str):
            return {"type": "final", "answer": answer}

        name = (
            decision.get("name")
            or decision.get("tool")
            or decision.get("tool_name")
            or decision.get("tool_call")
            or decision.get("action")
        )
        if isinstance(decision.get("function"), dict):
            name = name or decision["function"].get("name")
            arguments = decision.get("arguments") or decision["function"].get("arguments", {})
        else:
            arguments = decision.get("arguments") or decision.get("parameters", {})

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}

        if isinstance(name, str):
            return {"type": "tool_call", "name": name, "arguments": arguments}

        return decision

    @staticmethod
    def _tool_from_index(index: Any, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
        try:
            numeric_index = int(index)
        except (TypeError, ValueError):
            return None
        if 0 <= numeric_index < len(tools):
            return tools[numeric_index]
        return None

    @staticmethod
    def _guard_tool_by_request(
        selected_tool: dict[str, Any], question: str, tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Correct an index selection when the request contains a clear domain noun."""
        if Assistant._is_repository_listing_request(question):
            repository_tools = [
                tool for tool in tools if tool["name"] == "search_repositories"
            ]
            if repository_tools:
                return repository_tools[0]

        if Assistant._is_browser_request(question):
            url_tools = [
                tool for tool in tools
                if tool["name"].lower() in {"browser_navigate", "navigate"}
            ]
            if url_tools and selected_tool not in url_tools:
                return url_tools[0]

        question_tokens = Assistant._tokens(question)
        repository_listing = (
            "repository" in question_tokens
            and bool(question_tokens & {"list", "name", "all", "show", "find", "search"})
            and not bool(
                question_tokens
                & {
                    "collaborator", "collaborators", "contributor", "contributors",
                    "branch", "release", "issue", "commit", "pull_request",
                }
            )
            and not re.search(
                r"(?<![\w-])[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?![\w-])",
                question,
            )
        )
        if repository_listing:
            repository_tools = [
                tool for tool in tools if tool["name"] == "search_repositories"
            ]
            if repository_tools:
                return repository_tools[0]

        schema_request = question_tokens & {
            "column", "field", "schema", "describe", "structure"
        }
        if schema_request:
            schema_tools = [
                tool
                for tool in tools
                if "table" in tool.get("input_schema", {}).get("required", [])
                and (
                    "describe" in tool["name"].lower()
                    or "column" in str(tool.get("description", "")).lower()
                    or "schema" in str(tool.get("description", "")).lower()
                )
            ]
            if len(schema_tools) == 1:
                return schema_tools[0]

        normalized_question = question.lower().replace("pull request", "pull_request")
        categories = {
            "branch": "branch",
            "release": "release",
            "issue": "issue",
            "commit": "commit",
            "pull_request": "pull_request",
        }
        requested_category = next(
            (category for word, category in categories.items() if word in normalized_question),
            None,
        )
        if not requested_category:
            return selected_tool

        selected_text = f"{selected_tool['name']} {selected_tool.get('description', '')}".lower()
        if requested_category in selected_text:
            return selected_tool

        candidates = [
            tool
            for tool in tools
            if requested_category
            in f"{tool['name']} {tool.get('description', '')}".lower()
        ]
        list_candidates = [tool for tool in candidates if tool["name"].startswith("list_")]
        return list_candidates[0] if len(list_candidates) == 1 else selected_tool

    @staticmethod
    def _resolve_tool_name(name: Any, tools: list[dict[str, Any]]) -> str | None:
        if not isinstance(name, str):
            return None
        names = [tool["name"] for tool in tools]
        if name in names:
            return name
        lowered = {tool_name.lower(): tool_name for tool_name in names}
        return lowered.get(name.lower())

    @staticmethod
    def _tokens(value: str) -> set[str]:
        """Normalize text for generic retrieval without a tool-specific rule table."""
        tokens = set(re.findall(r"[a-z0-9]+", value.lower()))
        normalized: set[str] = set()
        for token in tokens:
            if token in {"repo", "repos"}:
                normalized.add("repository")
            elif token in {"db", "dbs"}:
                normalized.add("database")
            elif token.endswith("ies") and len(token) > 4:
                normalized.add(f"{token[:-3]}y")
            elif token.endswith("ches") and len(token) > 5:
                normalized.add(token[:-2])
            elif token.endswith("s") and len(token) > 3:
                normalized.add(token[:-1])
            else:
                normalized.add(token)
        return normalized

    @classmethod
    def _retrieve_tools(
        cls, question: str, tools: list[dict[str, Any]], limit: int = 5
    ) -> list[dict[str, Any]]:
        """Return the most relevant live MCP tools for a user request."""
        if cls._is_repository_listing_request(question):
            repository_search = next(
                (tool for tool in tools if tool["name"] == "search_repositories"),
                None,
            )
            if repository_search:
                return [repository_search]
        if cls._is_browser_request(question):
            browser_tools = [tool for tool in tools if cls._is_browser_tool(tool)]
            if browser_tools:
                tools = browser_tools
        scored = [
            (cls._tool_score(question, tool), tool["name"], tool) for tool in tools
        ]

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [tool for _, _, tool in scored[:limit]]

    @classmethod
    def _is_repository_listing_request(cls, question: str) -> bool:
        tokens = cls._tokens(question)
        return (
            "repository" in tokens
            and bool(tokens & {"list", "name", "all", "show", "find", "search"})
            and "collaborator" not in tokens
            and "commit" not in tokens
            and "issue" not in tokens
            and "branch" not in tokens
            and not re.search(
                r"(?<![\w-])[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?![\w-])",
                question,
            )
        )

    @classmethod
    def _tool_score(cls, question: str, tool: dict[str, Any]) -> int:
        question_tokens = cls._tokens(question)
        name_tokens = cls._tokens(tool["name"])
        description_tokens = cls._tokens(tool.get("description", ""))
        schema_tokens = cls._tokens(
            " ".join(tool.get("input_schema", {}).get("required", []))
        )
        score = (
            12 * len(question_tokens & name_tokens)
            + 2 * len(question_tokens & description_tokens)
            + len(question_tokens & schema_tokens)
        )
        # Prefer the least-specialized matching tool. For example,
        # "list issues" should use list_issues, not list_issue_fields,
        # because "fields" was not part of the request. Search/get/list
        # are treated as generic read-action synonyms for this comparison.
        action_synonyms = {"list", "search", "find", "get", "show", "read"}
        unmatched_name_tokens = (name_tokens - question_tokens) - action_synonyms
        score -= 16 * len(unmatched_name_tokens)
        if question_tokens & {"list", "show", "find", "search", "get"}:
            if question_tokens & name_tokens:
                score += 3
            if name_tokens & {"create", "update", "merge", "push"}:
                score -= 6
        if cls._is_browser_request(question) and cls._is_browser_tool(tool):
            score += 30
            if re.search(r"https?://\S+", question, re.IGNORECASE) and tool["name"].lower() in {
                "browser_navigate",
                "navigate",
            }:
                score += 20
        return score

    @staticmethod
    def _is_browser_request(question: str) -> bool:
        """Recognize browser intent independently of the small model."""
        has_url = bool(re.search(r"https?://\S+", question, re.IGNORECASE))
        browser_context = {
            "page", "website", "login", "title", "heading", "click",
            "button", "form", "field", "visible", "screenshot", "browser", "url",
        }
        navigation_words = {"navigate", "visit", "browse"}
        tokens = Assistant._tokens(question)
        return (
            has_url
            or bool(tokens & browser_context)
            or bool(tokens & navigation_words)
            or ("open" in tokens and bool(tokens & browser_context))
        )

    @classmethod
    def _is_repository_listing_request(cls, question: str) -> bool:
        """Recognize repository listing without confusing collaborators with repos."""
        tokens = cls._tokens(question)
        return (
            "repository" in tokens
            and bool(tokens & {"list", "name", "all", "show", "find", "search"})
            and not bool(
                tokens
                & {
                    "collaborator", "collaboration", "member", "branch",
                    "release", "issue", "commit", "pull_request",
                }
            )
        )

    @staticmethod
    def _browser_navigation_needs_url(question: str) -> bool:
        navigation_words = {"navigate", "visit", "browse"}
        tokens = Assistant._tokens(question)
        return (
            bool(tokens & navigation_words)
            or ("open" in tokens and bool(tokens & {
                "page", "website", "login", "browser", "url"
            }))
        ) and not re.search(r"https?://\S+", question, re.IGNORECASE)

    @staticmethod
    def _is_browser_tool(tool: dict[str, Any]) -> bool:
        """Identify browser tools using live MCP metadata, not a fixed tool list."""
        server = str(tool.get("server", "")).lower()
        name = str(tool.get("name", "")).lower()
        description = str(tool.get("description", "")).lower()
        return (
            "playwright" in server
            or name.startswith("browser_")
            or "browser automation" in description
        )

    @classmethod
    def _route_server(
        cls, question: str, tools: list[dict[str, Any]]
    ) -> str | None:
        """Choose one MCP server before asking Qwen to choose a tool."""
        by_server: dict[str, list[dict[str, Any]]] = {}
        for tool in tools:
            by_server.setdefault(str(tool.get("server", "")), []).append(tool)

        if cls._is_browser_request(question):
            browser_server = next(
                (server for server in by_server if "playwright" in server.lower()),
                None,
            )
            if browser_server:
                return browser_server

        # Repository requests are unambiguous. Route them to the MCP server
        # that actually exposes search_repositories before generic scoring can
        # make a MySQL search/schema tool look relevant.
        if cls._is_repository_listing_request(question):
            github_server = next(
                (
                    server
                    for server, server_tools in by_server.items()
                    if any(tool.get("name") == "search_repositories" for tool in server_tools)
                ),
                None,
            )
            if github_server:
                return github_server

        github_tool_name = cls._github_read_tool_name(question)
        if github_tool_name:
            github_server = next(
                (
                    server
                    for server, server_tools in by_server.items()
                    if any(tool.get("name") == github_tool_name for tool in server_tools)
                ),
                None,
            )
            if github_server:
                return github_server

        question_tokens = cls._tokens(question)
        server_scores: list[tuple[int, str]] = []
        for server, server_tools in by_server.items():
            server_tokens = cls._tokens(server)
            score = 4 * len(question_tokens & server_tokens)
            tool_scores = sorted(
                (cls._tool_score(question, tool) for tool in server_tools),
                reverse=True,
            )
            score += sum(tool_scores[:3])
            server_scores.append((score, server))

        if not server_scores:
            return None
        best_score, best_server = max(server_scores, key=lambda item: (item[0], item[1]))
        return best_server if best_score > 0 else None

    @classmethod
    def _github_read_tool_name(cls, question: str) -> str | None:
        """Map clear GitHub read intents to one exact tool name."""
        tokens = cls._tokens(question)
        if "branch" in tokens:
            return "list_branches"
        if "collaborator" in tokens or "collaboration" in tokens:
            return "list_repository_collaborators"
        if "issue" in tokens and "field" not in tokens and "type" not in tokens:
            return "list_issues"
        if "release" in tokens:
            return "list_releases"
        if "commit" in tokens:
            return "list_commits"
        if "pull_request" in tokens or ("pull" in tokens and "request" in tokens):
            return "list_pull_requests"
        return None

    @staticmethod
    def _enrich_repository_arguments(
        arguments: dict[str, Any],
        question: str,
        tool: dict[str, Any],
        authenticated_user: str | None = None,
    ) -> dict[str, Any]:
        """Fill owner/repo required by GitHub tools from owner/repo in the question."""
        def clean_identifier(value: str) -> str:
            return value.strip().strip("`'\"").rstrip(".,;:!?)\]}\"")

        enriched = dict(arguments)
        repository = enriched.get("repository") or enriched.get("repo_name")
        if isinstance(repository, str) and "/" in repository:
            owner, repo = repository.strip().strip("`'\"").split("/", 1)
            enriched.setdefault("owner", clean_identifier(owner))
            enriched.setdefault("repo", clean_identifier(repo))

        match = re.search(r"(?<![\w-])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?![\w-])", question)
        required = tool.get("input_schema", {}).get("required", [])
        if match and ("owner" in required or "repo" in required):
            enriched.setdefault("owner", clean_identifier(match.group(1)))
            enriched.setdefault("repo", clean_identifier(match.group(2)))

        # If the user gave only a repository name, trust the authenticated
        # GitHub identity over a model-generated owner value. Preserve an
        # explicit owner only when the request contains owner/repo syntax.
        if "owner" in required and authenticated_user and not match:
            enriched["owner"] = authenticated_user
        if "repo" in required and not enriched.get("repo"):
            repository_match = re.search(
                r"(?:repository|repo)\s+([A-Za-z0-9_.-]+)",
                question,
                re.IGNORECASE,
            )
            if not repository_match:
                repository_match = re.search(
                    r"\bin\s+([A-Za-z0-9_.-]+)",
                    question,
                    re.IGNORECASE,
                )
            if not repository_match:
                repository_match = re.search(
                    r"\b([A-Za-z0-9_.-]+)\s+(?:repository|repo)\b",
                    question,
                    re.IGNORECASE,
                )
            if repository_match:
                enriched["repo"] = clean_identifier(repository_match.group(1))
        return enriched

    @staticmethod
    def _enrich_required_arguments(
        arguments: dict[str, Any],
        question: str,
        tool: dict[str, Any],
        authenticated_user: str | None = None,
    ) -> dict[str, Any]:
        """Derive common missing arguments from the request and MCP schema."""
        enriched = dict(arguments)
        required = tool.get("input_schema", {}).get("required", [])

        url_match = re.search(r"https?://\S+", question, re.IGNORECASE)
        if url_match:
            url = url_match.group(0).rstrip(".,;:!?)]}")
            if "url" in required and not enriched.get("url"):
                enriched["url"] = url
            if "uri" in required and not enriched.get("uri"):
                enriched["uri"] = url

        if "query" in required:
            user_match = re.search(
                r"(?:user|of)\s+([A-Za-z0-9-]+)", question, re.IGNORECASE
            )
            if user_match and user_match.group(1).lower().startswith("repositor"):
                user_match = None
            repository_match = re.search(
                r"(?<![\w-])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?![\w-])",
                question,
            )
            repository_listing = (
                "repositor" in question.lower()
                and any(word in question.lower() for word in ("list", "search", "all", "name"))
            )
            if (
                repository_listing
                and tool.get("name") == "search_repositories"
                and authenticated_user
                and not repository_match
            ):
                # For owner-less repository-list requests, the PAT identity is
                # authoritative. Do not treat words such as "list" or
                # "repositories" as a username extracted from the sentence.
                enriched["query"] = f"user:{authenticated_user}"
            elif user_match and tool.get("name") == "search_repositories":
                username = user_match.group(1)
                current_query = str(enriched.get("query", "")).strip()
                if not current_query or current_query.lower() == username.lower():
                    enriched["query"] = f"user:{username}"
            elif (
                repository_match
                and repository_listing
                and tool.get("name") == "search_repositories"
                and not enriched.get("query")
            ):
                enriched["query"] = f"user:{repository_match.group(1)}"
            elif repository_listing and tool.get("name") == "search_repositories":
                current_query = str(enriched.get("query", "")).strip()
                explicit_owner = user_match or repository_match
                if authenticated_user and not explicit_owner:
                    # For owner-less repository-list requests, the token's
                    # authenticated identity is authoritative. Never trust a
                    # random username/query generated by the small model.
                    enriched["query"] = f"user:{authenticated_user}"
            elif not enriched.get("query"):
                enriched["query"] = question.strip()

        if "state" in required and not enriched.get("state"):
            if "open" in question.lower():
                enriched["state"] = "open"

        if "database" in required:
            configured_database = settings.mysql_database
            placeholder_values = {
                "your_database_name",
                "database_name",
                "dbname",
                "your_database",
            }
            current_database = str(enriched.get("database", "")).strip()
            is_mysql_tool = (
                str(tool.get("server", "")).lower() == "mysql"
                or tool.get("name") in {
                    "list_databases", "list_tables", "describe_table", "run_query"
                }
            )
            if configured_database and is_mysql_tool:
                # The configured DSN is authoritative. Do not allow the model
                # to copy a table name or invent a database name here.
                enriched["database"] = configured_database
            elif configured_database and (
                not current_database
                or current_database.lower() in placeholder_values
            ):
                enriched["database"] = configured_database

        if "table" in required:
            explicit_table = Assistant._extract_explicit_table(question)
            if explicit_table:
                # User-provided table context is authoritative. This prevents
                # the model from turning a requested column such as AML or
                # status into the table argument.
                enriched["table"] = explicit_table

        return enriched

    @staticmethod
    def _extract_explicit_table(question: str) -> str | None:
        """Extract a table name from common natural-language table phrases."""
        identifier = r"[A-Za-z_][A-Za-z0-9_$]*"
        patterns = (
            rf"\b(?:of|from|in)\s+table\s+({identifier})\b",
            rf"\btable\s+({identifier})\b",
            rf"\b({identifier})\s+table\b",
            rf"\b(?:from|in)\s+({identifier})\b",
        )
        for pattern in patterns:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                candidate = match.group(1)
                if candidate.lower() not in {
                    "the", "this", "that", "each", "all", "any", "name", "names"
                }:
                    return candidate
        return None

    @staticmethod
    def _complete_repository_list_answer(
        question: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
    ) -> str | None:
        """Preserve every repository name when the user explicitly requests a list."""
        lowered = question.lower()
        if tool_name != "search_repositories" or not any(
            word in lowered for word in ("all", "list", "names", "repositories")
        ):
            return None
        if not str(arguments.get("query", "")).startswith("user:"):
            return None

        if not isinstance(result, list):
            return None
        for item in result:
            if not isinstance(item, str):
                continue
            try:
                payload = json.loads(item)
            except json.JSONDecodeError:
                continue
            repositories = payload.get("items")
            if isinstance(repositories, list):
                names = [
                    repository.get("name")
                    for repository in repositories
                    if isinstance(repository, dict) and repository.get("name")
                ]
                owner = str(arguments["query"])[len("user:"):]
                numbered_names = " ".join(
                    f"{index}. {name}." for index, name in enumerate(names, start=1)
                )
                return (
                    f"I found {len(names)} repositories owned by {owner}. "
                    + numbered_names
                )
        return None

    @staticmethod
    def _complete_table_list_answer(
        question: str, tool_name: str, result: Any
    ) -> str | None:
        """Format database table names directly instead of relying on Qwen."""
        if tool_name != "list_tables":
            return None
        if not isinstance(result, list):
            return None
        for item in result:
            if not isinstance(item, str):
                continue
            try:
                payload = json.loads(item)
            except json.JSONDecodeError:
                continue
            tables = payload.get("tables")
            if not isinstance(tables, list):
                continue
            physical_tables = [
                table for table in tables
                if isinstance(table, dict)
                and str(table.get("comment", "")).upper() != "VIEW"
            ]
            names = [
                table.get("name")
                for table in physical_tables
                if isinstance(table.get("name"), str)
            ]
            if not names:
                return "No tables were found in the requested database."
            numbered = " ".join(
                f"{index}. {name}." for index, name in enumerate(names, start=1)
            )
            return f"I found {len(names)} tables: {numbered}"
        return None

    @staticmethod
    def _complete_branch_list_answer(
        arguments: dict[str, Any], tool_name: str, result: Any
    ) -> str | None:
        """Format GitHub branch results directly instead of relying on Qwen."""
        if tool_name != "list_branches" or not isinstance(result, list):
            return None
        for item in result:
            if not isinstance(item, str):
                continue
            try:
                branches = json.loads(item)
            except json.JSONDecodeError:
                continue
            if not isinstance(branches, list):
                continue
            names = [
                branch.get("name")
                for branch in branches
                if isinstance(branch, dict) and isinstance(branch.get("name"), str)
            ]
            repository = arguments.get("repo", "the repository")
            if not names:
                return f"The repository {repository} has no branches."
            numbered = " ".join(
                f"{index}. {name}." for index, name in enumerate(names, start=1)
            )
            return f"The branches in repository {repository} are: {numbered}"
        return None

    @staticmethod
    def _requests_table_values(question: str) -> bool:
        tokens = Assistant._tokens(question)
        return bool(tokens & {"value", "values", "row", "rows", "data", "records"})

    @classmethod
    def _mysql_operation(cls, question: str) -> str | None:
        """Classify MySQL intent before the model sees any database tools."""
        tokens = cls._tokens(question)
        if (
            "table" in tokens
            and tokens & {"name", "names"}
            and tokens & {"list", "show", "all", "know", "provide"}
        ):
            return "list_tables"
        has_table = bool(re.search(r"\btable\s+[A-Za-z_][A-Za-z0-9_$]*", question, re.I))
        if tokens & {"partition", "partitions"}:
            return "list_partitions"
        if not has_table and "table" not in tokens:
            if tokens & {"database", "databases"} and tokens & {"list", "show", "name", "names"}:
                return "list_databases"
            if tokens & {"status", "variable", "variables"}:
                return "server_status"
            return "list_tables" if tokens & {"table", "tables"} else None
        asks_for_values = cls._requests_table_values(question)
        if asks_for_values or tokens & {
            "approved", "rejected", "pending", "status", "records"
        }:
            return "table_rows"
        if tokens & {"column", "columns", "field", "fields", "schema", "structure"}:
            return "describe_table"
        if tokens & {"count", "number", "howmany"} or re.search(
            r"\bhow\s+many\b", question, re.IGNORECASE
        ):
            return "count_rows"
        return "describe_table"

    @classmethod
    def _mysql_allowed_tool_names(cls, question: str) -> set[str] | None:
        """Return a narrow tool contract for the detected MySQL operation."""
        operation = cls._mysql_operation(question)
        if operation == "list_databases":
            return {"list_databases"}
        if operation == "list_tables":
            return {"list_tables"}
        if operation == "list_partitions":
            return {"list_partitions"}
        if operation == "describe_table":
            return {"describe_table"}
        if operation in {"table_rows", "count_rows"}:
            return {"describe_table", "run_query"}
        if operation == "server_status":
            return {"list_status", "list_variables", "show_status"}
        return None

    @staticmethod
    def _table_value_query(
        database: str, table: str, describe_result: Any
    ) -> dict[str, str] | None:
        """Build a read-only query from the columns returned by describe_table."""
        if not re.fullmatch(r"[A-Za-z0-9_$]+", database):
            return None
        if not re.fullmatch(r"[A-Za-z0-9_$]+", table):
            return None
        if not isinstance(describe_result, list):
            return None

        for item in describe_result:
            if not isinstance(item, str):
                continue
            try:
                payload = json.loads(item)
            except json.JSONDecodeError:
                continue
            columns = payload.get("columns")
            if not isinstance(columns, list):
                continue
            names = [
                column.get("name")
                for column in columns
                if isinstance(column, dict)
                and isinstance(column.get("name"), str)
                and re.fullmatch(r"[A-Za-z0-9_$]+", column["name"])
            ]
            if not names:
                return None
            selected_columns = ", ".join(f"`{name}`" for name in names)
            return {
                "database": database,
                "sql": (
                    f"SELECT {selected_columns} FROM `{database}`.`{table}` "
                    "LIMIT 5"
                ),
            }
        return None

    @classmethod
    def _table_data_query(
        cls, database: str, table: str, describe_result: Any, question: str
    ) -> dict[str, str] | None:
        """Build a safe read-only query for table rows or a filtered count."""
        base = cls._table_value_query(database, table, describe_result)
        if not base:
            return None
        columns: list[str] = []
        for item in describe_result:
            if not isinstance(item, str):
                continue
            try:
                payload = json.loads(item)
            except json.JSONDecodeError:
                continue
            raw_columns = payload.get("columns")
            if isinstance(raw_columns, list):
                columns = [
                    column["name"]
                    for column in raw_columns
                    if isinstance(column, dict)
                    and isinstance(column.get("name"), str)
                    and re.fullmatch(r"[A-Za-z0-9_$]+", column["name"])
                ]
                break
        if not columns:
            return None

        where = ""
        question_lower = question.lower()
        if "status" in columns and re.search(
            r"\b(status|state)\s*(?:is|=|equals|of)?\s*approved\b|\bapproved\b",
            question_lower,
        ):
            where = " WHERE `status` = 'approved'"
        elif "status" in columns and re.search(r"\b(rejected|pending)\b", question_lower):
            value = "rejected" if "rejected" in question_lower else "pending"
            where = f" WHERE `status` = '{value}'"

        # Do not send large document/blob payloads to the LLM or the UI.
        display_columns = [
            name for name in columns
            if name.lower() not in {
                "document", "document_data", "file_content", "payload", "base64"
            }
        ] or columns[:8]
        requested_columns = cls._extract_requested_columns(question)
        if requested_columns:
            requested_lower = [item.lower() for item in requested_columns]
            requested_names = set(requested_lower)
            # Natural language often separates one snake_case column into
            # words, for example "KYC status column" -> `KYC_status`.
            compound_parts: set[str] = set()
            for index in range(len(requested_lower) - 1):
                compound = f"{requested_lower[index]}_{requested_lower[index + 1]}"
                if any(name.lower() == compound for name in columns):
                    requested_names.add(compound)
                    compound_parts.update(
                        {requested_lower[index], requested_lower[index + 1]}
                    )
            requested_names.difference_update(compound_parts)
            display_columns = [
                name for name in columns if name.lower() in requested_names
            ]
            if not display_columns:
                return None
        selected_columns = ", ".join(f"`{name}`" for name in display_columns)
        sql = (
            f"SELECT {selected_columns} FROM `{database}`.`{table}`"
            f"{where} LIMIT 100"
        )
        return {"database": database, "sql": sql}

    @staticmethod
    def _extract_requested_column(question: str) -> str | None:
        """Backward-compatible helper returning the first requested column."""
        columns = Assistant._extract_requested_columns(question)
        return columns[0] if columns else None

    @staticmethod
    def _extract_requested_columns(question: str) -> list[str]:
        """Extract one or more columns mentioned before the word 'column'."""
        identifier = r"[A-Za-z_][A-Za-z0-9_$]*"
        match = re.search(
            rf"\b(.+?)(?:'s)?\s+(?:columns?|cloumns?|cloumn)\b",
            question,
            re.I,
        )
        if not match:
            return []
        excluded = {
            "all", "the", "name", "names", "other", "only", "each",
            "provide", "of", "values", "value", "and", "list", "show",
        }
        candidates = re.findall(identifier, match.group(1))
        return [candidate for candidate in candidates if candidate.lower() not in excluded]

    @classmethod
    def _table_count_query(
        cls, database: str, table: str, describe_result: Any, question: str
    ) -> dict[str, str] | None:
        """Build a count query using the described schema and request filter."""
        data_query = cls._table_data_query(database, table, describe_result, question)
        if not data_query:
            return None
        sql = data_query["sql"]
        where = ""
        where_match = re.search(r"( WHERE .+?)(?: LIMIT 100)?$", sql)
        if where_match:
            where = where_match.group(1)
        return {
            "database": database,
            "sql": f"SELECT COUNT(*) AS `row_count` FROM `{database}`.`{table}`{where}",
        }

    @staticmethod
    def _complete_table_values_answer(table: str, result: Any) -> str | None:
        """Format returned database rows directly instead of relying on Qwen."""
        if not isinstance(result, list):
            return None
        for item in result:
            if not isinstance(item, str):
                continue
            try:
                payload = json.loads(item)
            except json.JSONDecodeError:
                continue
            columns = payload.get("columns")
            rows = payload.get("rows")
            if not isinstance(columns, list) or not isinstance(rows, list):
                continue
            formatted_rows: list[str] = []
            for index, row in enumerate(rows, start=1):
                if not isinstance(row, list):
                    continue
                values = []
                for column, value in zip(columns, row):
                    values.append(f"{column}={value if value is not None else 'NULL'}")
                formatted_rows.append(f"{index}. " + ", ".join(values))
            if not formatted_rows:
                return f"The table {table} returned no rows."
            return (
                f"The first {len(formatted_rows)} rows from table {table} are: "
                + "; ".join(formatted_rows)
            )
        return None

    @staticmethod
    def _complete_count_answer(table: str, result: Any) -> str | None:
        if not isinstance(result, list):
            return None
        for item in result:
            if not isinstance(item, str):
                continue
            try:
                payload = json.loads(item)
            except json.JSONDecodeError:
                continue
            rows = payload.get("rows")
            if isinstance(rows, list) and rows and isinstance(rows[0], list) and rows[0]:
                return f"The table {table} contains {rows[0][0]} matching records."
        return None

    @staticmethod
    def _complete_filtered_table_answer(
        table: str, count_result: Any, rows_result: Any
    ) -> str | None:
        """Format a count and readable rows without exposing large payloads."""
        count: Any = None
        rows_payload: dict[str, Any] | None = None
        for result in (count_result, rows_result):
            if not isinstance(result, list):
                continue
            for item in result:
                if not isinstance(item, str):
                    continue
                try:
                    payload = json.loads(item)
                except json.JSONDecodeError:
                    continue
                rows = payload.get("rows")
                if not isinstance(rows, list):
                    continue
                if payload.get("columns") == ["row_count"] and rows and rows[0]:
                    count = rows[0][0]
                elif isinstance(payload.get("columns"), list):
                    rows_payload = payload
        if count is None or rows_payload is None:
            return None
        columns = rows_payload["columns"]
        formatted: list[str] = []
        for index, row in enumerate(rows_payload["rows"], start=1):
            if not isinstance(row, list):
                continue
            pairs = []
            for column, value in zip(columns, row):
                pairs.append(f"{column}: {value if value is not None else 'NULL'}")
            formatted.append(f"{index}. " + ", ".join(pairs))
        if not formatted:
            return f"There are {count} matching records in {table}, but no rows were returned."
        return (
            f"There are {count} matching records in {table}. "
            f"The returned records are: " + " ".join(formatted)
        )

    async def _improve_request_with_llm(self, question: str) -> dict[str, str | None]:
        """Rewrite the request and identify missing information before tools run."""
        normalized = self._improve_request(question)
        database_context = (
            f"A default MySQL database is configured: {settings.mysql_database}."
            if settings.mysql_database
            else "No default MySQL database is configured. Ask for the database name when a database request needs one."
        )
        prompt = (
            "Understand the user's request before tool selection. Do not guess missing information. "
            "If the request is sufficiently clear, rewrite it into one concise explicit instruction. "
            "If a required detail is missing or ambiguous, ask one concise clarification question "
            "and do not rewrite or answer the request. Preserve every email address, URL, repository "
            "name, database name, table name, column name, and quoted value exactly. "
            f"{database_context} Return only one of these JSON forms: "
            '{"status":"ready","rewritten_query":"..."} or '
            '{"status":"clarification","clarification":"..."}'
        )
        try:
            raw = await self.ollama.chat(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": normalized},
                ]
            )
            decision = parse_json_object(raw)
            if decision and decision.get("status") == "clarification":
                clarification = decision.get("clarification")
                if isinstance(clarification, str) and clarification.strip():
                    return {"query": normalized, "clarification": clarification.strip()}
            candidate = decision.get("rewritten_query") if decision else None
            if not isinstance(candidate, str):
                return {"query": normalized, "clarification": None}
            candidate = re.sub(r"\s+", " ", candidate.strip())
            if not candidate or len(candidate) > 10000:
                return {"query": normalized, "clarification": None}

            # Never accept a rewrite that drops an important literal.
            protected_values = re.findall(
                r"https?://\S+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b",
                normalized,
            )
            lowered_candidate = candidate.casefold()
            if any(value.casefold() not in lowered_candidate for value in protected_values):
                print(
                    "[TRACE] LLM rewrite rejected because it dropped a protected identifier",
                    flush=True,
                )
                return {"query": normalized, "clarification": None}
            explicit_table = self._extract_explicit_table(normalized)
            if explicit_table and explicit_table.casefold() not in lowered_candidate:
                print(
                    f"[TRACE] LLM rewrite rejected because it dropped table '{explicit_table}'",
                    flush=True,
                )
                return {"query": normalized, "clarification": None}
            return {"query": candidate, "clarification": None}
        except Exception as exc:
            print(f"[TRACE] LLM request improvement failed; using normalized request: {exc}", flush=True)
            return {"query": normalized, "clarification": None}

    @classmethod
    def _host_clarification_needed(cls, question: str) -> str | None:
        """Reject unresolvable database requests without inventing context."""
        operation = cls._mysql_operation(question)
        if not operation or operation in {"list_databases", "server_status"}:
            return None
        table = cls._extract_explicit_table(question)
        if operation in {"describe_table", "table_rows", "count_rows", "list_partitions"} and not table:
            return "Which MySQL table should I use?"
        if not settings.mysql_database and operation in {
            "list_tables", "describe_table", "table_rows", "count_rows", "list_partitions"
        }:
            has_database = bool(
                re.search(
                    r"(?:\b(?:database|schema)\s+[A-Za-z_][A-Za-z0-9_$]*\b|\b[A-Za-z_][A-Za-z0-9_$]*\s+(?:database|schema)\b)",
                    question,
                    re.IGNORECASE,
                )
            )
            if not has_database:
                return "Which database should I use for this table request?"
        return None

    @staticmethod
    def _has_explicit_database(question: str) -> bool:
        return bool(
            re.search(
                r"(?:\b(?:database|schema)\s+[A-Za-z_][A-Za-z0-9_$]*\b|\b[A-Za-z_][A-Za-z0-9_$]*\s+(?:database|schema)\b)",
                question,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_repository_reference(question: str) -> bool:
        return bool(
            re.search(
                r"(?<![\w-])[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?![\w-])",
                question,
            )
            or re.search(
                r"(?:\b(?:repository|repo)\s+[A-Za-z0-9_.-]+\b|\b[A-Za-z0-9_.-]+\s+(?:repository|repo)\b)",
                question,
                re.IGNORECASE,
            )
        )

    @classmethod
    def _structured_clarification(
        cls, question: str, authenticated_user: str | None
    ) -> str | None:
        """Return one precise question for the first missing required field."""
        if cls._browser_navigation_needs_url(question):
            return "Which website URL should I open?"

        mysql_operation = cls._mysql_operation(question)
        if mysql_operation:
            if mysql_operation == "list_databases":
                return None
            if mysql_operation == "list_tables":
                if not settings.mysql_database and not cls._has_explicit_database(question):
                    return "Which database should I list tables from?"
                return None
            if not cls._extract_explicit_table(question):
                return "Which MySQL table should I use?"
            if not settings.mysql_database and not cls._has_explicit_database(question):
                return "Which database contains this table?"
            if (
                mysql_operation == "table_rows"
                and cls._requests_table_values(question)
                and not cls._extract_requested_columns(question)
                and not re.search(r"\ball\s+columns?\b", question, re.IGNORECASE)
            ):
                return "Which column or columns should I retrieve?"
            return None

        github_tokens = cls._tokens(question)
        github_operation = github_tokens & {
            "branch", "issue", "commit", "release", "pull_request", "collaborator"
        }
        if github_operation and not cls._has_repository_reference(question):
            return "Which GitHub repository should I use? Please provide its name, such as CQRS-APP."
        if github_operation and not authenticated_user:
            return "Which GitHub owner should I use, and is GitHub authentication configured?"
        return None

    @classmethod
    def _clarification_is_unnecessary(cls, question: str, authenticated_user: str | None) -> bool:
        """Allow safe host defaults to override an over-cautious LLM question."""
        operation = cls._mysql_operation(question)
        if operation == "list_databases":
            return True
        if operation == "list_tables" and re.search(
            r"(?:\b(?:database|schema)\s+[A-Za-z_][A-Za-z0-9_$]*\b|\b[A-Za-z_][A-Za-z0-9_$]*\s+(?:database|schema)\b)",
            question,
            re.IGNORECASE,
        ):
            return True
        github_repository_reference = bool(
            re.search(
                r"(?<![\w-])[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?![\w-])",
                question,
            )
            or re.search(
                r"(?:\b(?:repository|repo)\s+[A-Za-z0-9_.-]+\b|\b[A-Za-z0-9_.-]+\s+(?:repository|repo)\b)",
                question,
                re.IGNORECASE,
            )
        )
        github_operation = bool(
            cls._tokens(question)
            & {"branch", "issue", "commit", "release", "pull_request", "collaborator"}
        )
        return bool(
            authenticated_user
            and (
                cls._is_repository_listing_request(question)
                or (github_repository_reference and github_operation)
            )
        )

    async def answer(self, question: str) -> dict[str, Any]:
        print(f"[TRACE] User query received by orchestrator: {question}", flush=True)
        # Keep deterministic intent extraction anchored to the user's words.
        # The LLM rewrite is for understanding only and must not replace the
        # table, column, database, URL, or filter information used by the host.
        intent_question = self._improve_request(question)
        structured_clarification = self._structured_clarification(
            intent_question, self.github.authenticated_user
        )
        if structured_clarification:
            print(
                f"[TRACE] Structured clarification required: {structured_clarification}",
                flush=True,
            )
            return {"answer": structured_clarification, "tool_calls": []}
        improvement = await self._improve_request_with_llm(intent_question)
        clarification = improvement.get("clarification")
        if clarification:
            if self._clarification_is_unnecessary(
                intent_question, self.github.authenticated_user
            ):
                print(
                    "[TRACE] Ignoring unnecessary clarification: host has enough context "
                    "to fulfill the request",
                    flush=True,
                )
            else:
                print(f"[TRACE] Clarification required: {clarification}", flush=True)
                return {"answer": clarification, "tool_calls": []}
        improved_question = str(improvement.get("query") or intent_question)
        print(f"[TRACE] Improved user query: {improved_question}", flush=True)
        question = improved_question
        host_clarification = self._host_clarification_needed(intent_question)
        if host_clarification:
            print(f"[TRACE] Host clarification required: {host_clarification}", flush=True)
            return {"answer": host_clarification, "tool_calls": []}
        all_tools = await self.github.tools()
        connected_servers: dict[str, list[str]] = {}
        for tool in all_tools:
            connected_servers.setdefault(str(tool.get("server", "unknown")), []).append(
                tool["name"]
            )
        print("[TRACE] Connected MCP servers and tools:", flush=True)
        for server, names in connected_servers.items():
            print(f"[TRACE]   {server}: {', '.join(names)}", flush=True)
        tool_calls: list[dict[str, Any]] = []
        if self._browser_navigation_needs_url(intent_question):
            return {
                "answer": "Please provide the website URL so I can navigate to its login page and inspect the visible form fields.",
                "tool_calls": tool_calls,
            }
        repository_listing = self._is_repository_listing_request(intent_question)
        repository_tool = next(
            (tool for tool in all_tools if tool.get("name") == "search_repositories"),
            None,
        )
        github_read_tool_name = self._github_read_tool_name(intent_question)
        github_read_tool = next(
            (
                tool
                for tool in all_tools
                if tool.get("name") == github_read_tool_name
            ),
            None,
        ) if github_read_tool_name else None
        repository_server = (
            str(repository_tool.get("server"))
            if repository_tool and repository_tool.get("server")
            else None
        )
        github_read_server = (
            str(github_read_tool.get("server"))
            if github_read_tool and github_read_tool.get("server")
            else None
        )
        if repository_listing and not repository_tool:
            print(
                "[TRACE] Repository intent detected, but search_repositories is unavailable",
                flush=True,
            )
            return {
                "answer": (
                    "This is a GitHub repository request, but the GitHub MCP server "
                    "does not expose the search_repositories tool."
                ),
                "tool_calls": tool_calls,
            }
        if github_read_tool_name and not github_read_tool:
            print(
                f"[TRACE] GitHub intent detected ({github_read_tool_name}), but the tool is unavailable",
                flush=True,
            )
            return {
                "answer": (
                    f"This is a GitHub {github_read_tool_name} request, but that tool "
                    "is not available from the connected GitHub MCP server."
                ),
                "tool_calls": tool_calls,
            }

        # A repository listing must never fall through to MySQL intent
        # detection, even if the small model invents database terminology or
        # selects a schema tool.
        mysql_intent = None if repository_listing else self._mysql_operation(intent_question)
        mysql_server = next(
            (
                str(tool.get("server"))
                for tool in all_tools
                if "mysql" in str(tool.get("server", "")).lower()
            ),
            None,
        )
        if mysql_intent and not mysql_server:
            print(
                f"[TRACE] MySQL intent detected ({mysql_intent}), but MySQL MCP is unavailable",
                flush=True,
            )
            return {
                "answer": (
                    "The request requires MySQL, but the MySQL MCP server is currently "
                    "unavailable. Please check its connection and try again."
                ),
                "tool_calls": tool_calls,
            }
        selected_server = (
            repository_server
            if repository_listing
            else github_read_server
            if github_read_tool_name
            else mysql_server or self._route_server(intent_question, all_tools)
        )
        print(f"[TRACE] Host detected MCP server intent: {selected_server or 'none'}", flush=True)
        if selected_server and hasattr(self.github, "tools_for_server"):
            server_tools = await self.github.tools_for_server(selected_server)
        else:
            server_tools = all_tools
        if repository_listing:
            repository_tools = [
                tool for tool in server_tools if tool["name"] == "search_repositories"
            ]
            if not repository_tools and repository_tool:
                repository_tools = [repository_tool]
            if repository_tools:
                server_tools = repository_tools
                print(
                    "[TRACE] Host restricted repository-list request to: search_repositories",
                    flush=True,
                )
        elif github_read_tool_name:
            github_tools = [
                tool for tool in server_tools if tool["name"] == github_read_tool_name
            ]
            if not github_tools and github_read_tool:
                github_tools = [github_read_tool]
            if github_tools:
                server_tools = github_tools
                print(
                    f"[TRACE] Host restricted GitHub request to: {github_read_tool_name}",
                    flush=True,
                )
        mysql_operation = (
            self._mysql_operation(intent_question)
            if selected_server and "mysql" in selected_server.lower()
            else None
        )
        mysql_allowed = self._mysql_allowed_tool_names(intent_question) if mysql_operation else None
        if mysql_allowed:
            server_tools = [
                tool for tool in server_tools if tool["name"] in mysql_allowed
            ]
            print(
                f"[TRACE] Host detected MySQL operation: {mysql_operation}; "
                f"allowed tools: {', '.join(tool['name'] for tool in server_tools) or 'none'}",
                flush=True,
            )
        print(
            f"[TRACE] Tools connected to selected MCP server ({selected_server or 'all'}): "
            f"{', '.join(tool['name'] for tool in server_tools) or 'none'}",
            flush=True,
        )
        candidate_tools = self._retrieve_tools(intent_question, server_tools)
        print(
            f"[TRACE] Tools offered to Qwen: {', '.join(tool['name'] for tool in candidate_tools) or 'none'}",
            flush=True,
        )
        tool_catalog = [
            {
                "tool_index": index,
                "name": tool["name"],
                "description": tool.get("description", ""),
                "required_arguments": tool.get("input_schema", {}).get("required", []),
            }
            for index, tool in enumerate(candidate_tools)
        ]
        tool_text = json.dumps(tool_catalog, default=str)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\nAvailable tools:\n{tool_text}"},
            {"role": "user", "content": question},
        ]

        for _ in range(4):
            raw = await self.ollama.chat(messages)
            decision = parse_json_object(raw)
            if not decision:
                return {"answer": raw, "tool_calls": tool_calls}
            decision = self._normalize_decision(decision)

            if decision.get("type") == "final":
                return {
                    "answer": str(decision.get("answer", "")),
                    "tool_calls": tool_calls,
                }

            if decision.get("type") != "tool_call":
                return {"answer": raw, "tool_calls": tool_calls}

            requested_name = decision.get("name")
            selected_tool = self._tool_from_index(
                decision.get("tool_index"), candidate_tools
            )
            # Compatibility for older responses that still contain a tool name.
            if selected_tool is None and requested_name:
                resolved_name = self._resolve_tool_name(requested_name, candidate_tools)
                selected_tool = next(
                    (tool for tool in candidate_tools if tool["name"] == resolved_name),
                    None,
                )
            if selected_tool and candidate_tools:
                best_tool = candidate_tools[0]
                selected_score = self._tool_score(intent_question, selected_tool)
                best_score = self._tool_score(intent_question, best_tool)
                # Override only a clearly weak model choice; close scores remain
                # under model control because wording can be genuinely ambiguous.
                if best_score - selected_score >= 4:
                    selected_tool = best_tool
            if mysql_operation in {"table_rows", "count_rows"}:
                describe_tool = next(
                    (tool for tool in candidate_tools if tool["name"] == "describe_table"),
                    None,
                )
                if describe_tool:
                    selected_tool = describe_tool
            name = selected_tool["name"] if selected_tool else None
            arguments = decision.get("arguments", {})
            print(
                f"[TRACE] Qwen selected candidate: {name or 'invalid'}; "
                f"arguments: {self._debug_value(arguments)}",
                flush=True,
            )
            if name is None:
                available = ", ".join(tool["name"] for tool in candidate_tools)
                return {
                    "answer": (
                        "I could not safely execute that GitHub operation. "
                        f"The model selected invalid tool_index '{decision.get('tool_index')}'. "
                        f"Available tools: {available}"
                    ),
                    "tool_calls": tool_calls,
                }
            if not isinstance(arguments, dict):
                return {
                    "answer": "I could not safely execute that GitHub operation because the arguments were invalid JSON.",
                    "tool_calls": tool_calls,
                }

            selected_tool = self._guard_tool_by_request(
                selected_tool, intent_question, candidate_tools
            )
            name = selected_tool["name"]
            print(f"[TRACE] Host final tool selection: {name}", flush=True)
            tool_definition = selected_tool
            arguments = self._enrich_repository_arguments(
                arguments,
                intent_question,
                tool_definition,
                self.github.authenticated_user,
            )
            arguments = self._enrich_required_arguments(
                arguments,
                intent_question,
                tool_definition,
                self.github.authenticated_user,
            )
            properties = tool_definition.get("input_schema", {}).get("properties", {})
            if properties:
                arguments = {
                    key: value for key, value in arguments.items() if key in properties
                }
            query = str(arguments.get("query", "")).strip()
            if name == "search_commits" and re.fullmatch(
                r"user:[A-Za-z0-9_.-]+", query
            ):
                return {
                    "answer": (
                        "The search_commits tool requires search text, not only a user qualifier. "
                        "Use a phrase such as 'search commits containing authentication' or ask to list commits."
                    ),
                    "tool_calls": tool_calls,
                }
            required_arguments = tool_definition.get("input_schema", {}).get("required", [])
            missing_arguments = [
                field for field in required_arguments if field not in arguments
            ]
            if missing_arguments:
                return {
                    "answer": (
                        f"I need these details before calling '{name}': "
                        f"{', '.join(missing_arguments)}"
                    ),
                    "tool_calls": tool_calls,
                }
            print(
                f"[TRACE] Selected tool: {name}; arguments: {self._debug_value(arguments)}",
                flush=True,
            )
            call_record = {"name": name, "arguments": arguments}
            tool_calls.append(call_record)

            # Prevent a small model from repeating the exact same call forever.
            if any(
                previous["name"] == name and previous["arguments"] == arguments
                for previous in tool_calls[:-1]
            ):
                return {
                    "answer": (
                        f"The tool '{name}' was requested repeatedly. "
                        "The previous tool result was not converted into a final answer."
                    ),
                    "tool_calls": tool_calls,
                }

            try:
                result = await self.github.call(name, arguments)
            except Exception as exc:
                call_record["error"] = str(exc)
                schema = tool_definition.get("input_schema", {})
                messages.extend(
                    [
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": (
                                f"TOOL_ERROR for {name}: {exc}\n"
                                f"The attempted arguments were: {json.dumps(arguments, default=str)}\n"
                                f"The exact required input schema is: {json.dumps(schema, default=str)}\n"
                                "Repair the arguments using the original user request and retry "
                                "with a valid tool_call. Do not invent a tool name."
                            ),
                        },
                    ]
                )
                continue
            call_record["result"] = result
            print(f"[TRACE] Tool result: {self._debug_value(result)}", flush=True)

            if name == "describe_table" and mysql_operation in {"table_rows", "count_rows"}:
                data_arguments = self._table_data_query(
                    str(arguments.get("database", "")),
                    str(arguments.get("table", "")),
                    result,
                    intent_question,
                )
                count_arguments = self._table_count_query(
                    str(arguments.get("database", "")),
                    str(arguments.get("table", "")),
                    result,
                    intent_question,
                )
                if data_arguments and count_arguments:
                    count_call = {
                        "name": "run_query",
                        "arguments": count_arguments,
                    }
                    tool_calls.append(count_call)
                    print(
                        f"[TRACE] Host follow-up tool: run_query count; arguments: "
                        f"{self._debug_value(count_arguments)}",
                        flush=True,
                    )
                    try:
                        count_result = await self.github.call("run_query", count_arguments)
                        count_call["result"] = count_result
                        print(
                            f"[TRACE] Tool result: {self._debug_value(count_result)}",
                            flush=True,
                        )
                    except Exception as exc:
                        count_call["error"] = str(exc)
                        count_result = None
                        print(f"[TRACE] Count query failed: {exc}", flush=True)

                    value_call = {
                        "name": "run_query",
                        "arguments": data_arguments,
                    }
                    tool_calls.append(value_call)
                    print(
                        f"[TRACE] Host follow-up tool: run_query; arguments: "
                        f"{self._debug_value(data_arguments)}",
                        flush=True,
                    )
                    try:
                        value_result = await self.github.call("run_query", data_arguments)
                        value_call["result"] = value_result
                        print(
                            f"[TRACE] Tool result: {self._debug_value(value_result)}",
                            flush=True,
                        )
                        direct_answer = self._complete_filtered_table_answer(
                            str(arguments.get("table", "")),
                            count_result,
                            value_result,
                        )
                        if direct_answer is not None:
                            return {
                                "answer": direct_answer,
                                "tool_calls": tool_calls,
                            }
                    except Exception as exc:
                        value_call["error"] = str(exc)
                        print(
                            f"[TRACE] Follow-up run_query failed: {exc}",
                            flush=True,
                        )
            complete_answer = self._complete_repository_list_answer(
                intent_question, name, arguments, result
            )
            if complete_answer is not None:
                return {"answer": complete_answer, "tool_calls": tool_calls}
            complete_answer = self._complete_table_list_answer(
                intent_question, name, result
            )
            if complete_answer is not None:
                return {"answer": complete_answer, "tool_calls": tool_calls}
            complete_answer = self._complete_branch_list_answer(
                arguments, name, result
            )
            if complete_answer is not None:
                return {"answer": complete_answer, "tool_calls": tool_calls}
            result_text = json.dumps(result, default=str)
            messages.extend(
                [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            f"TOOL_RESULT for {name}: {result_text}\n"
                            "The tool call is complete. Do not call the same tool again. "
                            "Now return one final JSON response with type=final and answer."
                        ),
                    },
                ]
            )

        return {
            "answer": "The request required too many tool steps. Please try a more specific question.",
            "tool_calls": tool_calls,
        }
