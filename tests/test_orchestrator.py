import unittest

from app.config import settings
from app.orchestrator import Assistant


class ToolRetrievalTests(unittest.TestCase):
    def test_request_is_improved_before_intent_detection(self) -> None:
        question = "  opnen this page   https://example.com  "
        self.assertEqual(
            Assistant._improve_request(question),
            "open this page https://example.com",
        )

    def test_request_improvement_preserves_repository_identifiers(self) -> None:
        question = "list branches of SAMIRURMA1994/CQRS-APP"
        self.assertEqual(Assistant._improve_request(question), question)

    def test_branch_request_ranks_branch_tool_first(self) -> None:
        question = "list of branch name of repository SAMIRURMA1994/CQRS-APP"
        tools = [
            {"name": "search_repositories", "description": "Search repositories"},
            {"name": "list_releases", "description": "List releases"},
            {"name": "list_branches", "description": "List branches"},
        ]
        ranked = Assistant._retrieve_tools(question, tools)
        self.assertEqual(ranked[0]["name"], "list_branches")
        self.assertGreaterEqual(
            Assistant._tool_score(question, ranked[0])
            - Assistant._tool_score(question, tools[1]),
            8,
        )

    def test_repository_listing_ranks_repository_search_first(self) -> None:
        question = "search all repository names of user SAMIRURMA1994"
        tools = [
            {"name": "get_tag", "description": "Get a tag"},
            {"name": "search_repositories", "description": "Search repositories"},
            {"name": "list_issues", "description": "List issues"},
        ]
        ranked = Assistant._retrieve_tools(question, tools)
        self.assertEqual(ranked[0]["name"], "search_repositories")

    def test_generic_repository_listing_avoids_collaborator_tool(self) -> None:
        question = "list of repository"
        tools = [
            {
                "name": "list_repository_collaborators",
                "description": "List repository collaborators",
            },
            {
                "name": "search_repositories",
                "description": "Search repositories",
            },
        ]
        ranked = Assistant._retrieve_tools(question, tools)
        self.assertEqual(ranked[0]["name"], "search_repositories")

    def test_repository_listing_guard_rejects_collaborator_tool(self) -> None:
        question = "i want to know list of repositories"
        collaborator = {
            "name": "list_repository_collaborators",
            "description": "List repository collaborators",
        }
        search = {
            "name": "search_repositories",
            "description": "Search repositories",
        }
        selected = Assistant._guard_tool_by_request(collaborator, question, [collaborator, search])
        self.assertEqual(selected["name"], "search_repositories")

    def test_repository_listing_exposes_only_repository_search(self) -> None:
        question = "i want to known list of repositories"
        tools = [
            {"name": "list_repository_collaborators", "description": "List repository collaborators"},
            {"name": "search_repositories", "description": "Search repositories"},
        ]
        ranked = Assistant._retrieve_tools(question, tools)
        self.assertEqual([tool["name"] for tool in ranked], ["search_repositories"])

    def test_repository_listing_extracts_owner_after_of(self) -> None:
        question = "search all repository name of SAMIRURMA1994"
        tool = {
            "name": "search_repositories",
            "input_schema": {"required": ["query"]},
        }
        arguments = Assistant._enrich_required_arguments(
            {"query": "SAMIRURMA1994"}, question, tool
        )
        self.assertEqual(arguments, {"query": "user:SAMIRURMA1994"})

    def test_user_qualifier_is_not_added_to_commit_search(self) -> None:
        question = "search commits by user SAMIRURMA1994 in SAMIRURMA1994/CQRS-APP"
        tool = {"name": "search_commits", "input_schema": {"required": ["query"]}}
        arguments = Assistant._enrich_required_arguments({}, question, tool)
        self.assertEqual(arguments["query"], question)

    def test_mysql_uses_database_from_config_when_model_invents_placeholder(self) -> None:
        tool = {
            "name": "list_tables",
            "server": "MySQL",
            "input_schema": {"required": ["database"]},
        }
        arguments = Assistant._enrich_required_arguments(
            {"database": "your_database_name"},
            "provide all table names in the database",
            tool,
        )
        if settings.mysql_database:
            self.assertNotEqual(arguments["database"], "your_database_name")
        else:
            self.assertEqual(arguments["database"], "your_database_name")

    def test_mysql_uses_configured_database_when_model_copies_table_name(self) -> None:
        tool = {
            "name": "describe_table",
            "server": "MySQL",
            "input_schema": {"required": ["database", "table"]},
        }
        arguments = Assistant._enrich_required_arguments(
            {
                "database": "Corporate_User_KYB_MM",
                "table": "Corporate_User_KYB_MM",
            },
            "provide all column name of this table Corporate_User_KYB_MM",
            tool,
        )
        if settings.mysql_database:
            self.assertEqual(arguments["database"], "KYCManagement_DB")
        else:
            self.assertEqual(arguments["database"], "Corporate_User_KYB_MM")
        self.assertEqual(arguments["table"], "Corporate_User_KYB_MM")

    def test_explicit_table_overrides_model_column_as_table(self) -> None:
        question = "provide all AML column values of each record of table KYC"
        tool = {
            "name": "describe_table",
            "server": "MySQL",
            "input_schema": {"required": ["database", "table"]},
        }
        arguments = Assistant._enrich_required_arguments(
            {"database": "KYCManagement_DB", "table": "AML"},
            question,
            tool,
        )
        self.assertEqual(arguments["table"], "KYC")

    def test_table_suffix_form_is_extracted(self) -> None:
        self.assertEqual(
            Assistant._extract_explicit_table(
                "how many status are approved in Corporate_User_KYB_MM table"
            ),
            "Corporate_User_KYB_MM",
        )

    def test_column_request_prefers_describe_table(self) -> None:
        question = "provide all column name of this table Corporate_User_KYB_MM"
        tools = [
            {
                "name": "list_tables",
                "server": "MySQL",
                "input_schema": {"required": ["database"]},
            },
            {
                "name": "describe_table",
                "server": "MySQL",
                "description": "Describe table columns and schema",
                "input_schema": {"required": ["database", "table"]},
            },
        ]
        selected = Assistant._guard_tool_by_request(tools[0], question, tools)
        self.assertEqual(selected["name"], "describe_table")

    def test_table_value_query_uses_described_columns(self) -> None:
        result = [
            '{"columns":[{"name":"id"},{"name":"email"},{"name":"created_at"}]}'
        ]
        query = Assistant._table_value_query(
            "KYCManagement_DB",
            "kyc_issuer_approval",
            result,
        )
        self.assertEqual(
            query,
            {
                "database": "KYCManagement_DB",
                "sql": "SELECT `id`, `email`, `created_at` FROM `KYCManagement_DB`.`kyc_issuer_approval` LIMIT 5",
            },
        )

    def test_table_values_answer_contains_actual_rows(self) -> None:
        result = [
            '{"columns":["id","email"],"rows":[[7,"satyakee@test.com"],[8,"kushal@yopmail.com"]]}'
        ]
        answer = Assistant._complete_table_values_answer("kyc_issuer_approval", result)
        self.assertIn("id=7", answer)
        self.assertIn("email=satyakee@test.com", answer)
        self.assertIn("id=8", answer)

    def test_mysql_table_status_request_excludes_server_status_tools(self) -> None:
        question = "show approved records from table Corporate_User_KYB_MM"
        self.assertEqual(Assistant._mysql_operation(question), "table_rows")
        self.assertEqual(
            Assistant._mysql_allowed_tool_names(question),
            {"describe_table", "run_query"},
        )

    def test_filtered_table_query_uses_actual_status_column(self) -> None:
        result = [
            '{"columns":[{"name":"id"},{"name":"status"},{"name":"email"}]}'
        ]
        query = Assistant._table_data_query(
            "KYCManagement_DB",
            "Corporate_User_KYB_MM",
            result,
            "show approved records from table Corporate_User_KYB_MM",
        )
        self.assertEqual(
            query,
            {
                "database": "KYCManagement_DB",
                "sql": "SELECT `id`, `status`, `email` FROM `KYCManagement_DB`.`Corporate_User_KYB_MM` WHERE `status` = 'approved' LIMIT 100",
            },
        )

    def test_count_query_is_formatted_from_database_result(self) -> None:
        result = [
            '{"columns":["row_count"],"rows":[[7]]}'
        ]
        answer = Assistant._complete_count_answer("Corporate_User_KYB_MM", result)
        self.assertEqual(
            answer,
            "The table Corporate_User_KYB_MM contains 7 matching records.",
        )

    def test_column_values_request_is_data_intent(self) -> None:
        question = "provide all AML column values of each record of table KYC"
        self.assertEqual(Assistant._mysql_operation(question), "table_rows")
        self.assertEqual(Assistant._extract_requested_column(question), "AML")

    def test_column_values_query_selects_only_requested_column(self) -> None:
        result = [
            '{"columns":[{"name":"email"},{"name":"AML"},{"name":"status"}]}'
        ]
        query = Assistant._table_data_query(
            "KYCManagement_DB",
            "KYC",
            result,
            "provide all AML column values of each record of table KYC",
        )
        self.assertEqual(
            query,
            {
                "database": "KYCManagement_DB",
                "sql": "SELECT `AML` FROM `KYCManagement_DB`.`KYC` LIMIT 100",
            },
        )

    def test_multiple_requested_columns_are_selected(self) -> None:
        question = "provide all AML,email column values of each record from table KYC"
        self.assertEqual(
            Assistant._extract_requested_columns(question), ["AML", "email"]
        )
        result = [
            '{"columns":[{"name":"email"},{"name":"AML"},{"name":"status"}]}'
        ]
        query = Assistant._table_data_query(
            "KYCManagement_DB", "KYC", result, question
        )
        self.assertEqual(
            query["sql"],
            "SELECT `email`, `AML` FROM `KYCManagement_DB`.`KYC` LIMIT 100",
        )

    def test_split_words_match_snake_case_column(self) -> None:
        question = "list all KYC status cloumn value of table pyrros_kyc_corporate"
        result = [
            '{"columns":[{"name":"email"},{"name":"kyc_status"},{"name":"status"}]}'
        ]
        query = Assistant._table_data_query(
            "KYCManagement_DB", "pyrros_kyc_corporate", result, question
        )
        self.assertEqual(
            query["sql"],
            "SELECT `kyc_status` FROM `KYCManagement_DB`.`pyrros_kyc_corporate` LIMIT 100",
        )

    def test_table_names_request_is_not_a_table_schema_request(self) -> None:
        question = "i want to know all table name of database KYCManagement_DB"
        self.assertEqual(Assistant._mysql_operation(question), "list_tables")
        self.assertIsNone(Assistant._extract_explicit_table(question))

    def test_explicit_database_table_list_does_not_need_clarification(self) -> None:
        self.assertTrue(
            Assistant._clarification_is_unnecessary(
                "Please provide the list of table names for the MMUserManagementV3 database.",
                None,
            )
        )

    def test_github_repository_operation_uses_authenticated_owner(self) -> None:
        self.assertTrue(
            Assistant._clarification_is_unnecessary(
                "Please provide the github all branch name of repository CQRS-APP.",
                "SAMIRURMA1994",
            )
        )

    def test_repository_before_repository_word_is_resolved(self) -> None:
        question = "Please provide branch name of CQRS-APP repository."
        tool = {
            "name": "list_branches",
            "input_schema": {"required": ["owner", "repo"]},
        }
        arguments = Assistant._enrich_repository_arguments(
            {}, question, tool, authenticated_user="SAMIRURMA1994"
        )
        self.assertEqual(
            arguments,
            {"owner": "SAMIRURMA1994", "repo": "CQRS-APP"},
        )

    def test_structured_clarification_for_missing_github_repository(self) -> None:
        answer = Assistant._structured_clarification(
            "Please provide list of CQRS branch name of github",
            "SAMIRURMA1994",
        )
        self.assertIn("Which GitHub repository", answer)

    def test_structured_clarification_for_missing_mysql_database(self) -> None:
        answer = Assistant._structured_clarification(
            "list all table names", None
        )
        if not settings.mysql_database:
            self.assertIn("Which database", answer)

    def test_db_abbreviation_requests_database_list(self) -> None:
        self.assertEqual(
            Assistant._mysql_operation("list of db name"),
            "list_databases",
        )

    def test_table_list_answer_contains_all_table_names(self) -> None:
        result = [
            '{"tables":[{"name":"KYC","rows":2025},{"name":"pyrros_kyc_corporate","rows":28}]}'
        ]
        answer = Assistant._complete_table_list_answer(
            "list all table names of database KYCManagement_DB",
            "list_tables",
            result,
        )
        self.assertEqual(
            answer,
            "I found 2 tables: 1. KYC. 2. pyrros_kyc_corporate.",
        )

    def test_branch_list_answer_contains_branch_names(self) -> None:
        result = ['[{"name":"main","sha":"abc","protected":false}]']
        answer = Assistant._complete_branch_list_answer(
            {"owner": "SAMIRURMA1994", "repo": "CQRS-APP"},
            "list_branches",
            result,
        )
        self.assertEqual(
            answer,
            "The branches in repository CQRS-APP are: 1. main.",
        )

    def test_repository_listing_uses_authenticated_user(self) -> None:
        question = "list all repository names"
        tool = {
            "name": "search_repositories",
            "input_schema": {"required": ["query"]},
        }
        arguments = Assistant._enrich_required_arguments(
            {}, question, tool, authenticated_user="SAMIRURMA1994"
        )
        self.assertEqual(arguments, {"query": "user:SAMIRURMA1994"})

    def test_generic_repository_listing_does_not_need_clarification(self) -> None:
        self.assertTrue(
            Assistant._clarification_is_unnecessary(
                "i want to known list of repositories",
                "SAMIRURMA1994",
            )
        )

    def test_repository_listing_excludes_collaborator_operation(self) -> None:
        self.assertTrue(Assistant._is_repository_listing_request("list of repositories"))
        self.assertFalse(
            Assistant._is_repository_listing_request(
                "list repository collaborators for SAMIRURMA1994/CQRS-APP"
            )
        )

    def test_repository_listing_replaces_random_model_query(self) -> None:
        question = "list of repositories"
        tool = {
            "name": "search_repositories",
            "input_schema": {"required": ["query"]},
        }
        arguments = Assistant._enrich_required_arguments(
            {"query": "user:random-user"},
            question,
            tool,
            authenticated_user="SAMIRURMA1994",
        )
        self.assertEqual(arguments, {"query": "user:SAMIRURMA1994"})

    def test_repository_listing_uses_authenticated_user_for_generic_phrase(self) -> None:
        question = "i want name of list of repositories"
        tool = {
            "name": "search_repositories",
            "input_schema": {"required": ["query"]},
        }
        arguments = Assistant._enrich_required_arguments(
            {"query": "repositories"},
            question,
            tool,
            authenticated_user="SAMIRURMA1994",
        )
        self.assertEqual(arguments, {"query": "user:SAMIRURMA1994"})

    def test_repository_request_uses_authenticated_owner(self) -> None:
        question = "list issues in repository CQRS-APP"
        tool = {
            "input_schema": {"required": ["owner", "repo"]},
        }
        arguments = Assistant._enrich_repository_arguments(
            {}, question, tool, authenticated_user="SAMIRURMA1994"
        )
        self.assertEqual(
            arguments,
            {"owner": "SAMIRURMA1994", "repo": "CQRS-APP"},
        )

    def test_repository_only_request_overrides_model_owner(self) -> None:
        question = "list branches of repository CQRS-APP"
        tool = {
            "input_schema": {"required": ["owner", "repo"]},
        }
        arguments = Assistant._enrich_repository_arguments(
            {"owner": "CQRS-APP", "repo": "CQRS-APP"},
            question,
            tool,
            authenticated_user="SAMIRURMA1994",
        )
        self.assertEqual(
            arguments,
            {"owner": "SAMIRURMA1994", "repo": "CQRS-APP"},
        )

    def test_issue_request_ranks_issue_tool_first(self) -> None:
        question = "list open issues in SAMIRURMA1994/CQRS-APP"
        tools = [
            {"name": "list_branches", "description": "List branches"},
            {"name": "list_issue_fields", "description": "List issue fields"},
            {"name": "list_issues", "description": "List issues"},
            {"name": "list_releases", "description": "List releases"},
        ]
        ranked = Assistant._retrieve_tools(question, tools)
        self.assertEqual(ranked[0]["name"], "list_issues")

    def test_browser_request_excludes_github_tools(self) -> None:
        question = "open https://heyfloe.ai/hustlewithai and tell me the page title"
        tools = [
            {"name": "get_me", "server": "GitHub", "description": "Get current user"},
            {
                "name": "browser_navigate",
                "server": "Playwright",
                "description": "Navigate to a URL using browser automation",
            },
            {
                "name": "browser_snapshot",
                "server": "Playwright",
                "description": "Get the current page accessibility snapshot",
            },
        ]
        ranked = Assistant._retrieve_tools(question, tools)
        self.assertTrue(ranked)
        self.assertNotIn("get_me", [tool["name"] for tool in ranked])
        self.assertEqual(ranked[0]["name"], "browser_navigate")

    def test_browser_request_is_detected(self) -> None:
        self.assertTrue(
            Assistant._is_browser_request(
                "opnen this page https://heyfloe.ai/hustlewithai and tell me the title"
            )
        )
        self.assertTrue(
            Assistant._is_browser_request(
                "Navigate to the login page and describe all visible form fields"
            )
        )

    def test_browser_navigation_without_url_is_rejected_before_tool_call(self) -> None:
        self.assertTrue(
            Assistant._browser_navigation_needs_url(
                "Navigate to the login page and describe all visible form fields"
            )
        )

    def test_server_router_selects_playwright_for_browser_request(self) -> None:
        question = "open https://example.com and tell me the title"
        tools = [
            {"name": "get_me", "server": "GitHub", "description": "Get current user"},
            {
                "name": "browser_navigate",
                "server": "Playwright",
                "description": "Navigate to a URL using browser automation",
            },
        ]
        self.assertEqual(Assistant._route_server(question, tools), "Playwright")

    def test_server_router_selects_github_for_repository_request(self) -> None:
        question = "list open issues in SAMIRURMA1994/CQRS-APP"
        tools = [
            {
                "name": "list_issues",
                "server": "GitHub",
                "description": "List issues in a repository",
            },
            {
                "name": "browser_navigate",
                "server": "Playwright",
                "description": "Navigate to a URL using browser automation",
            },
        ]
        self.assertEqual(Assistant._route_server(question, tools), "GitHub")

    def test_server_router_does_not_send_repository_listing_to_mysql(self) -> None:
        question = "list of repositories"
        tools = [
            {
                "name": "search_repositories",
                "server": "GitHub",
                "description": "Search repositories",
            },
            {
                "name": "search_schema",
                "server": "MySQL",
                "description": "Search database schema",
            },
        ]
        self.assertEqual(Assistant._route_server(question, tools), "GitHub")

    def test_server_router_sends_branch_request_to_github(self) -> None:
        question = "list of branch name of repository CORS-APP"
        tools = [
            {
                "name": "list_branches",
                "server": "GitHub",
                "description": "List repository branches",
            },
            {
                "name": "list_partitions",
                "server": "MySQL",
                "description": "List table partitions",
            },
        ]
        self.assertEqual(Assistant._route_server(question, tools), "GitHub")
        self.assertEqual(Assistant._github_read_tool_name(question), "list_branches")

    def test_partition_request_allows_only_partition_tool(self) -> None:
        self.assertEqual(
            Assistant._mysql_operation("list partitions of table orders"),
            "list_partitions",
        )
        self.assertEqual(
            Assistant._mysql_allowed_tool_names("list partitions of table orders"),
            {"list_partitions"},
        )

    def test_tool_call_field_is_normalized(self) -> None:
        decision = Assistant._normalize_decision(
            {
                "tool_call": "browser_navigate",
                "arguments": {"url": "https://heyfloe.ai"},
            }
        )
        self.assertEqual(decision["type"], "tool_call")
        self.assertEqual(decision["name"], "browser_navigate")
        self.assertEqual(decision["arguments"]["url"], "https://heyfloe.ai")


if __name__ == "__main__":
    unittest.main()
