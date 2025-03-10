import logging
import os
import warnings
from functools import wraps
from typing import Any, Dict, List, Optional, Union

import httpx

from mem0.memory.setup import get_user_id, setup_config
from mem0.memory.telemetry import capture_client_event

logger = logging.getLogger(__name__)

# Setup user config
setup_config()


class APIError(Exception):
    """Exception raised for errors in the API."""

    pass


def api_error_handler(func):
    """Decorator to handle API errors consistently."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error occurred: {e}")
            raise APIError(f"API request failed: {e.response.text}")
        except httpx.RequestError as e:
            logger.error(f"Request error occurred: {e}")
            raise APIError(f"Request failed: {str(e)}")

    return wrapper


class MemoryClient:
    """Client for interacting with the Mem0 API.

    This class provides methods to create, retrieve, search, and delete memories
    using the Mem0 API.

    Attributes:
        api_key (str): The API key for authenticating with the Mem0 API.
        host (str): The base URL for the Mem0 API.
        client (httpx.Client): The HTTP client used for making API requests.
        organization (str, optional): (Deprecated) Organization name.
        project (str, optional): (Deprecated) Project name.
        org_id (str, optional): Organization ID.
        project_id (str, optional): Project ID.
        user_id (str): Unique identifier for the user.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        host: Optional[str] = None,
        organization: Optional[str] = None,
        project: Optional[str] = None,
        org_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        """Initialize the MemoryClient.

        Args:
            api_key: The API key for authenticating with the Mem0 API. If not provided,
                     it will attempt to use the MEM0_API_KEY environment variable.
            host: The base URL for the Mem0 API. Defaults to "https://api.mem0.ai".
            organization: (Deprecated) The name of the organization. Use org_id instead.
            project: (Deprecated) The name of the project. Use project_id instead.
            org_id: The ID of the organization.
            project_id: The ID of the project.

        Raises:
            ValueError: If no API key is provided or found in the environment.
        """
        self.api_key = api_key or os.getenv("MEM0_API_KEY")
        self.host = host or "https://api.mem0.ai"
        self.organization = organization
        self.project = project
        self.org_id = org_id
        self.project_id = project_id
        self.user_id = get_user_id()

        if not self.api_key:
            raise ValueError("Mem0 API Key not provided. Please provide an API Key.")

        if organization or project:
            warnings.warn(
                "Using 'organization' and 'project' parameters is deprecated and will be removed in version 0.1.40. "
                "Please use 'org_id' and 'project_id' instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        self.client = httpx.Client(
            base_url=self.host,
            headers={"Authorization": f"Token {self.api_key}", "Mem0-User-ID": self.user_id},
            timeout=60,
        )
        self._validate_api_key()
        capture_client_event("client.init", self)

    def _validate_api_key(self):
        """Validate the API key by making a test request."""
        try:
            params = self._prepare_params()
            response = self.client.get("/v1/ping/", params=params)
            response.raise_for_status()

            if response.status_code == 200:
                data = response.json()
                if data.get("org_id") and data.get("project_id"):
                    self.org_id = data.get("org_id")
                    self.project_id = data.get("project_id")

        except httpx.HTTPStatusError:
            raise ValueError("Invalid API Key. Please get a valid API Key from https://app.mem0.ai")

    @api_error_handler
    def add(self, messages: Union[str, List[Dict[str, str]]], **kwargs) -> Dict[str, Any]:
        """Add a new memory.

        Args:
            messages: Either a string message or a list of message dictionaries.
            **kwargs: Additional parameters such as user_id, agent_id, app_id, metadata, filters.

        Returns:
            A dictionary containing the API response.

        Raises:
            APIError: If the API request fails.
        """
        kwargs = self._prepare_params(kwargs)
        payload = self._prepare_payload(messages, kwargs)
        response = self.client.post("/v1/memories/", json=payload)
        response.raise_for_status()
        if "metadata" in kwargs:
            del kwargs["metadata"]
        capture_client_event("client.add", self, {"keys": list(kwargs.keys())})
        return response.json()

    @api_error_handler
    def get(self, memory_id: str) -> Dict[str, Any]:
        """Retrieve a specific memory by ID.

        Args:
            memory_id: The ID of the memory to retrieve.

        Returns:
            A dictionary containing the memory data.

        Raises:
            APIError: If the API request fails.
        """
        params = self._prepare_params()
        response = self.client.get(f"/v1/memories/{memory_id}/", params=params)
        response.raise_for_status()
        capture_client_event("client.get", self, {"memory_id": memory_id})
        return response.json()

    @api_error_handler
    def get_all(self, version: str = "v1", **kwargs) -> List[Dict[str, Any]]:
        """Retrieve all memories, with optional filtering.

        Args:
            version: The API version to use for the search endpoint.
            **kwargs: Optional parameters for filtering (user_id, agent_id, app_id, limit).

        Returns:
            A list of dictionaries containing memories.

        Raises:
            APIError: If the API request fails.
        """
        params = self._prepare_params(kwargs)
        if version == "v1":
            response = self.client.get(f"/{version}/memories/", params=params)
        elif version == "v2":
            if "page" in params and "page_size" in params:
                query_params = {"page": params.pop("page"), "page_size": params.pop("page_size")}
                response = self.client.post(f"/{version}/memories/", json=params, params=query_params)
            else:
                response = self.client.post(f"/{version}/memories/", json=params)
        response.raise_for_status()
        if "metadata" in kwargs:
            del kwargs["metadata"]
        capture_client_event(
            "client.get_all",
            self,
            {"api_version": version, "keys": list(kwargs.keys())},
        )
        return response.json()

    @api_error_handler
    def search(self, query: str, version: str = "v1", **kwargs) -> List[Dict[str, Any]]:
        """Search memories based on a query.

        Args:
            query: The search query string.
            version: The API version to use for the search endpoint.
            **kwargs: Additional parameters such as user_id, agent_id, app_id, limit, filters.

        Returns:
            A list of dictionaries containing search results.

        Raises:
            APIError: If the API request fails.
        """
        payload = {"query": query}
        params = self._prepare_params(kwargs)
        payload.update(params)
        response = self.client.post(f"/{version}/memories/search/", json=payload)
        response.raise_for_status()
        if "metadata" in kwargs:
            del kwargs["metadata"]
        capture_client_event("client.search", self, {"api_version": version, "keys": list(kwargs.keys())})
        return response.json()

    @api_error_handler
    def update(self, memory_id: str, data: str) -> Dict[str, Any]:
        """
        Update a memory by ID.
        Args:
            memory_id (str): Memory ID.
            data (str): Data to update in the memory.
        Returns:
            Dict[str, Any]: The response from the server.
        """
        capture_client_event("client.update", self, {"memory_id": memory_id})
        params = self._prepare_params()
        response = self.client.put(f"/v1/memories/{memory_id}/", json={"text": data}, params=params)
        response.raise_for_status()
        return response.json()

    @api_error_handler
    def delete(self, memory_id: str) -> Dict[str, Any]:
        """Delete a specific memory by ID.

        Args:
            memory_id: The ID of the memory to delete.

        Returns:
            A dictionary containing the API response.

        Raises:
            APIError: If the API request fails.
        """
        params = self._prepare_params()
        response = self.client.delete(f"/v1/memories/{memory_id}/", params=params)
        response.raise_for_status()
        capture_client_event("client.delete", self, {"memory_id": memory_id})
        return response.json()

    @api_error_handler
    def delete_all(self, **kwargs) -> Dict[str, str]:
        """Delete all memories, with optional filtering.

        Args:
            **kwargs: Optional parameters for filtering (user_id, agent_id, app_id).

        Returns:
            A dictionary containing the API response.

        Raises:
            APIError: If the API request fails.
        """
        params = self._prepare_params(kwargs)
        response = self.client.delete("/v1/memories/", params=params)
        response.raise_for_status()
        capture_client_event("client.delete_all", self, {"keys": list(kwargs.keys())})
        return response.json()

    @api_error_handler
    def history(self, memory_id: str) -> List[Dict[str, Any]]:
        """Retrieve the history of a specific memory.

        Args:
            memory_id: The ID of the memory to retrieve history for.

        Returns:
            A list of dictionaries containing the memory history.

        Raises:
            APIError: If the API request fails.
        """
        params = self._prepare_params()
        response = self.client.get(f"/v1/memories/{memory_id}/history/", params=params)
        response.raise_for_status()
        capture_client_event("client.history", self, {"memory_id": memory_id})
        return response.json()

    @api_error_handler
    def users(self) -> Dict[str, Any]:
        """Get all users, agents, and sessions for which memories exist."""
        params = self._prepare_params()
        response = self.client.get("/v1/entities/", params=params)
        response.raise_for_status()
        capture_client_event("client.users", self)
        return response.json()

    @api_error_handler
    def delete_users(self) -> Dict[str, str]:
        """Delete all users, agents, or sessions."""
        params = self._prepare_params()
        entities = self.users()
        for entity in entities["results"]:
            response = self.client.delete(f"/v1/entities/{entity['type']}/{entity['id']}/", params=params)
            response.raise_for_status()

        capture_client_event("client.delete_users", self)
        return {"message": "All users, agents, and sessions deleted."}

    @api_error_handler
    def reset(self) -> Dict[str, str]:
        """Reset the client by deleting all users and memories.

        This method deletes all users, agents, sessions, and memories associated with the client.

        Returns:
            Dict[str, str]: Message client reset successful.

        Raises:
            APIError: If the API request fails.
        """
        # Delete all users, agents, and sessions
        # This will also delete the memories
        self.delete_users()

        capture_client_event("client.reset", self)
        return {"message": "Client reset successful. All users and memories deleted."}

    @api_error_handler
    def batch_update(self, memories: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Batch update memories.

        Args:
            memories: List of memory dictionaries to update. Each dictionary must contain:
                - memory_id (str): ID of the memory to update
                - text (str): New text content for the memory

        Returns:
            str: Message indicating the success of the batch update.

        Raises:
            APIError: If the API request fails.
        """
        response = self.client.put("/v1/batch/", json={"memories": memories})
        response.raise_for_status()

        capture_client_event("client.batch_update", self)
        return response.json()

    @api_error_handler
    def batch_delete(self, memories: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Batch delete memories.

        Args:
            memories: List of memory dictionaries to delete. Each dictionary must contain:
                - memory_id (str): ID of the memory to delete

        Returns:
            str: Message indicating the success of the batch deletion.

        Raises:
            APIError: If the API request fails.
        """
        response = self.client.request("DELETE", "/v1/batch/", json={"memories": memories})
        response.raise_for_status()

        capture_client_event("client.batch_delete", self)
        return response.json()

    @api_error_handler
    def create_memory_export(self, schema: str, **kwargs) -> Dict[str, Any]:
        """Create a memory export with the provided schema.

        Args:
            schema: JSON schema defining the export structure
            **kwargs: Optional filters like user_id, run_id, etc.

        Returns:
            Dict containing export request ID and status message
        """
        response = self.client.post("/v1/exports/", json={"schema": schema, **self._prepare_params(kwargs)})
        response.raise_for_status()
        capture_client_event("client.create_memory_export", self, {"schema": schema, "keys": list(kwargs.keys())})
        return response.json()

    @api_error_handler
    def get_memory_export(self, **kwargs) -> Dict[str, Any]:
        """Get a memory export.

        Args:
            **kwargs: Filters like user_id to get specific export

        Returns:
            Dict containing the exported data
        """
        response = self.client.get("/v1/exports/", params=self._prepare_params(kwargs))
        response.raise_for_status()
        capture_client_event("client.get_memory_export", self, {"keys": list(kwargs.keys())})
        return response.json()

    @api_error_handler
    def get_project(self, fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Get instructions or categories for the current project.

        Args:
            fields: List of fields to retrieve

        Returns:
            Dictionary containing the requested fields.

        Raises:
            APIError: If the API request fails.
            ValueError: If org_id or project_id are not set.
        """
        if not (self.org_id and self.project_id):
            raise ValueError("org_id and project_id must be set to access instructions or categories")

        params = self._prepare_params({"fields": fields})
        response = self.client.get(
            f"/api/v1/orgs/organizations/{self.org_id}/projects/{self.project_id}/",
            params=params,
        )
        response.raise_for_status()
        capture_client_event("client.get_project_details", self, {"fields": fields})
        return response.json()

    @api_error_handler
    def update_project(
        self, custom_instructions: Optional[str] = None, custom_categories: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Update the project settings.

        Args:
            custom_instructions: New instructions for the project
            custom_categories: New categories for the project

        Returns:
            Dictionary containing the API response.

        Raises:
            APIError: If the API request fails.
            ValueError: If org_id or project_id are not set.
        """
        if not (self.org_id and self.project_id):
            raise ValueError("org_id and project_id must be set to update instructions or categories")

        if custom_instructions is None and custom_categories is None:
            raise ValueError(
                "Currently we only support updating custom_instructions or custom_categories, so you must provide at least one of them"
            )

        payload = self._prepare_params(
            {"custom_instructions": custom_instructions, "custom_categories": custom_categories}
        )
        response = self.client.patch(
            f"/api/v1/orgs/organizations/{self.org_id}/projects/{self.project_id}/",
            json=payload,
        )
        response.raise_for_status()
        capture_client_event(
            "client.update_project",
            self,
            {"custom_instructions": custom_instructions, "custom_categories": custom_categories},
        )
        return response.json()

    def chat(self):
        """Start a chat with the Mem0 AI. (Not implemented)

        Raises:
            NotImplementedError: This method is not implemented yet.
        """
        raise NotImplementedError("Chat is not implemented yet")

    def _prepare_payload(
        self, messages: Union[str, List[Dict[str, str]], None], kwargs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Prepare the payload for API requests.

        Args:
            messages: The messages to include in the payload.
            kwargs: Additional keyword arguments to include in the payload.

        Returns:
            A dictionary containing the prepared payload.
        """
        payload = {}
        if isinstance(messages, str):
            payload["messages"] = [{"role": "user", "content": messages}]
        elif isinstance(messages, list):
            payload["messages"] = messages

        payload.update({k: v for k, v in kwargs.items() if v is not None})
        return payload

    def _prepare_params(self, kwargs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Prepare query parameters for API requests.

        Args:
            kwargs: Keyword arguments to include in the parameters.

        Returns:
            A dictionary containing the prepared parameters.

        Raises:
            ValueError: If both org_id/project_id and org_name/project_name are provided.
        """

        if kwargs is None:
            kwargs = {}

        has_new = bool(self.org_id or self.project_id)
        has_old = bool(self.organization or self.project)

        if has_new and has_old:
            raise ValueError(
                "Please use either org_id/project_id or org_name/project_name, not both. "
                "Note that org_name/project_name are deprecated."
            )

        # Add org_id and project_id if both are available
        if self.org_id and self.project_id:
            kwargs["org_id"] = self.org_id
            kwargs["project_id"] = self.project_id
        elif self.org_id or self.project_id:
            raise ValueError("Please provide both org_id and project_id")

        # Add deprecated org_name and project_name if both are available
        if self.organization and self.project:
            kwargs["org_name"] = self.organization
            kwargs["project_name"] = self.project
        elif self.organization or self.project:
            raise ValueError("Please provide both org_name and project_name")

        return {k: v for k, v in kwargs.items() if v is not None}


class AsyncMemoryClient:
    """Asynchronous client for interacting with the Mem0 API.

    This class provides asynchronous versions of all MemoryClient methods.
    It uses httpx.AsyncClient for making non-blocking API requests.

    Attributes:
        sync_client (MemoryClient): Underlying synchronous client instance.
        async_client (httpx.AsyncClient): Async HTTP client for making API requests.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        host: Optional[str] = None,
        organization: Optional[str] = None,
        project: Optional[str] = None,
        org_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        self.sync_client = MemoryClient(api_key, host, organization, project, org_id, project_id)
        self.async_client = httpx.AsyncClient(
            base_url=self.sync_client.host,
            headers=self.sync_client.client.headers,
            timeout=60,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.async_client.aclose()

    @api_error_handler
    async def add(self, messages: Union[str, List[Dict[str, str]]], **kwargs) -> Dict[str, Any]:
        kwargs = self.sync_client._prepare_params(kwargs)
        payload = self.sync_client._prepare_payload(messages, kwargs)
        response = await self.async_client.post("/v1/memories/", json=payload)
        response.raise_for_status()
        if "metadata" in kwargs:
            del kwargs["metadata"]
        capture_client_event("async_client.add", self.sync_client, {"keys": list(kwargs.keys())})
        return response.json()

    @api_error_handler
    async def get(self, memory_id: str) -> Dict[str, Any]:
        params = self.sync_client._prepare_params()
        response = await self.async_client.get(f"/v1/memories/{memory_id}/", params=params)
        response.raise_for_status()
        capture_client_event("async_client.get", self.sync_client, {"memory_id": memory_id})
        return response.json()

    @api_error_handler
    async def get_all(self, version: str = "v1", **kwargs) -> List[Dict[str, Any]]:
        params = self.sync_client._prepare_params(kwargs)
        if version == "v1":
            response = await self.async_client.get(f"/{version}/memories/", params=params)
        elif version == "v2":
            response = await self.async_client.post(f"/{version}/memories/", json=params)
        response.raise_for_status()
        if "metadata" in kwargs:
            del kwargs["metadata"]
        capture_client_event(
            "async_client.get_all", self.sync_client, {"api_version": version, "keys": list(kwargs.keys())}
        )
        return response.json()

    @api_error_handler
    async def search(self, query: str, version: str = "v1", **kwargs) -> List[Dict[str, Any]]:
        payload = {"query": query}
        payload.update(self.sync_client._prepare_params(kwargs))
        response = await self.async_client.post(f"/{version}/memories/search/", json=payload)
        response.raise_for_status()
        if "metadata" in kwargs:
            del kwargs["metadata"]
        capture_client_event(
            "async_client.search", self.sync_client, {"api_version": version, "keys": list(kwargs.keys())}
        )
        return response.json()

    @api_error_handler
    async def update(self, memory_id: str, data: str) -> Dict[str, Any]:
        params = self.sync_client._prepare_params()
        response = await self.async_client.put(f"/v1/memories/{memory_id}/", json={"text": data}, params=params)
        response.raise_for_status()
        capture_client_event("async_client.update", self.sync_client, {"memory_id": memory_id})
        return response.json()

    @api_error_handler
    async def delete(self, memory_id: str) -> Dict[str, Any]:
        params = self.sync_client._prepare_params()
        response = await self.async_client.delete(f"/v1/memories/{memory_id}/", params=params)
        response.raise_for_status()
        capture_client_event("async_client.delete", self.sync_client, {"memory_id": memory_id})
        return response.json()

    @api_error_handler
    async def delete_all(self, **kwargs) -> Dict[str, str]:
        params = self.sync_client._prepare_params(kwargs)
        response = await self.async_client.delete("/v1/memories/", params=params)
        response.raise_for_status()
        capture_client_event("async_client.delete_all", self.sync_client, {"keys": list(kwargs.keys())})
        return response.json()

    @api_error_handler
    async def history(self, memory_id: str) -> List[Dict[str, Any]]:
        params = self.sync_client._prepare_params()
        response = await self.async_client.get(f"/v1/memories/{memory_id}/history/", params=params)
        response.raise_for_status()
        capture_client_event("async_client.history", self.sync_client, {"memory_id": memory_id})
        return response.json()

    @api_error_handler
    async def users(self) -> Dict[str, Any]:
        params = self.sync_client._prepare_params()
        response = await self.async_client.get("/v1/entities/", params=params)
        response.raise_for_status()
        capture_client_event("async_client.users", self.sync_client)
        return response.json()

    @api_error_handler
    async def delete_users(self) -> Dict[str, str]:
        params = self.sync_client._prepare_params()
        entities = await self.users()
        for entity in entities["results"]:
            response = await self.async_client.delete(f"/v1/entities/{entity['type']}/{entity['id']}/", params=params)
            response.raise_for_status()
        capture_client_event("async_client.delete_users", self.sync_client)
        return {"message": "All users, agents, and sessions deleted."}

    @api_error_handler
    async def reset(self) -> Dict[str, str]:
        await self.delete_users()
        capture_client_event("async_client.reset", self.sync_client)
        return {"message": "Client reset successful. All users and memories deleted."}

    @api_error_handler
    async def batch_update(self, memories: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Batch update memories.

        Args:
            memories: List of memory dictionaries to update. Each dictionary must contain:
                - memory_id (str): ID of the memory to update
                - text (str): New text content for the memory

        Returns:
            str: Message indicating the success of the batch update.

        Raises:
            APIError: If the API request fails.
        """
        response = await self.async_client.put("/v1/batch/", json={"memories": memories})
        response.raise_for_status()

        capture_client_event("async_client.batch_update", self.sync_client)
        return response.json()

    @api_error_handler
    async def batch_delete(self, memories: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Batch delete memories.

        Args:
            memories: List of memory dictionaries to delete. Each dictionary must contain:
                - memory_id (str): ID of the memory to delete

        Returns:
            str: Message indicating the success of the batch deletion.

        Raises:
            APIError: If the API request fails.
        """
        response = await self.async_client.request("DELETE", "/v1/batch/", json={"memories": memories})
        response.raise_for_status()

        capture_client_event("async_client.batch_delete", self.sync_client)
        return response.json()

    @api_error_handler
    async def create_memory_export(self, schema: str, **kwargs) -> Dict[str, Any]:
        """Create a memory export with the provided schema.

        Args:
            schema: JSON schema defining the export structure
            **kwargs: Optional filters like user_id, run_id, etc.

        Returns:
            Dict containing export request ID and status message
        """
        response = await self.async_client.post("/v1/exports/", json={"schema": schema, **self._prepare_params(kwargs)})
        response.raise_for_status()
        capture_client_event(
            "async_client.create_memory_export", self.sync_client, {"schema": schema, "keys": list(kwargs.keys())}
        )
        return response.json()

    @api_error_handler
    async def get_memory_export(self, **kwargs) -> Dict[str, Any]:
        """Get a memory export.

        Args:
            **kwargs: Filters like user_id to get specific export

        Returns:
            Dict containing the exported data
        """
        response = await self.async_client.get("/v1/exports/", params=self._prepare_params(kwargs))
        response.raise_for_status()
        capture_client_event("async_client.get_memory_export", self.sync_client, {"keys": list(kwargs.keys())})
        return response.json()

    @api_error_handler
    async def get_project(self, fields: Optional[List[str]] = None) -> Dict[str, Any]:
        if not (self.sync_client.org_id and self.sync_client.project_id):
            raise ValueError("org_id and project_id must be set to access instructions or categories")

        params = self._prepare_params({"fields": fields})
        response = await self.async_client.get(
            f"/api/v1/orgs/organizations/{self.sync_client.org_id}/projects/{self.sync_client.project_id}/",
            params=params,
        )
        response.raise_for_status()
        capture_client_event("async_client.get_project", self.sync_client, {"fields": fields})
        return response.json()

    @api_error_handler
    async def update_project(
        self, custom_instructions: Optional[str], custom_categories: Optional[List[str]]
    ) -> Dict[str, Any]:
        if not (self.sync_client.org_id and self.sync_client.project_id):
            raise ValueError("org_id and project_id must be set to update instructions or categories")

        payload = self.sync_client._prepare_params(
            {"custom_instructions": custom_instructions, "custom_categories": custom_categories}
        )
        response = await self.async_client.patch(
            f"/api/v1/orgs/organizations/{self.sync_client.org_id}/projects/{self.sync_client.project_id}/",
            json=payload,
        )
        response.raise_for_status()
        capture_client_event(
            "async_client.update_project",
            self.sync_client,
            {"custom_instructions": custom_instructions, "custom_categories": custom_categories},
        )
        return response.json()

    async def chat(self):
        raise NotImplementedError("Chat is not implemented yet")
