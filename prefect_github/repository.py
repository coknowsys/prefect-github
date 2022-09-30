"""
This is a module containing:
GitHub query_repository* tasks and the GitHub storage block.
"""

# This module was auto-generated using prefect-collection-generator so
# manually editing this file is not recommended. If this module
# is outdated, rerun scripts/generate.py.

import io
import shutil
from datetime import datetime
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from typing import Any, Dict, Iterable, Optional

from prefect import task
from prefect.filesystems import ReadableDeploymentStorage
from prefect.utilities.processutils import run_process
from pydantic import Field, validator
from sgqlc.operation import Operation

from prefect_github import GitHubCredentials
from prefect_github.exceptions import InvalidRepositoryURLError
from prefect_github.graphql import _execute_graphql_op, _subset_return_fields
from prefect_github.schemas import graphql_schema
from prefect_github.utils import initialize_return_fields_defaults, strip_kwargs

config_path = Path(__file__).parent.resolve() / "configs" / "query" / "repository.json"
return_fields_defaults = initialize_return_fields_defaults(config_path)


class GitHubRepository(ReadableDeploymentStorage):
    """
    Interact with files stored on GitHub repositories.
    """

    _block_type_name = "GitHub Repository"
    _logo_url = "https://images.ctfassets.net/gm98wzqotmnx/187oCWsD18m5yooahq1vU0/ace41e99ab6dc40c53e5584365a33821/github.png?h=250"  # noqa: E501

    repository: str = Field(
        default=...,
        description=(
            "The URL of a GitHub repository to read from, in either HTTPS or SSH "
            "format. If you are using a private repo, it must be in the HTTPS format."
        ),
    )
    reference: Optional[str] = Field(
        default=None,
        description="An optional reference to pin to; can be a branch name or tag.",
    )
    credentials: Optional[GitHubCredentials] = Field(
        default=None,
        description="An optional GitHubCredentials block for using private GitHub repos.",  # noqa: E501
    )

    @validator("credentials")
    def _ensure_credentials_go_with_https(cls, v: str, values: dict):
        """Ensure that credentials are not provided with 'SSH' formatted GitHub URLs."""
        if v is not None:
            if not values["repository"].startswith("https://"):
                raise InvalidRepositoryURLError(
                    (
                        "Crendentials can only be used with GitHub repositories "
                        "using the 'HTTPS' format. You must either remove the "
                        "credential if you wish to use the 'SSH' format and are not "
                        "using a private repository, or you must change the repository "
                        "url to the 'HTTPS' format. "
                    )
                )

        return v

    def _create_repo_url(self) -> str:
        """Format the URL provided to the `git clone` command.

        For private repos: https://<oauth-key>@github.com/<username>/<repo>.git
        All other repos should be the same as `self.repository`.
        """

        if self.repository.startswith("https://") and self.credentials is not None:
            repo_url = self.repository[8:]
            token_value = self.credentials.token.get_secret_value()
            full_url = f"https://{token_value}@{repo_url}"
        else:
            full_url = self.repository

        return full_url

    async def get_directory(
        self, from_path: str = None, local_path: str = None
    ) -> None:
        """
        Clones a GitHub project specified in `from_path` to the provided `local_path`;
        defaults to cloning the repository reference configured on the Block to the
        present working directory.

        Args:
            from_path: If provided, interpreted as a subdirectory of the underlying
                repository that will be copied to the provided local path.
            local_path: A local path to clone to; defaults to present working directory.
        """

        # CONSTRUCT COMMAND
        cmd = f"git clone {self._create_repo_url()}"
        if self.reference:
            cmd += f" -b {self.reference} --depth 1"

        if local_path is None:
            local_path = Path(".").absolute()
        else:
            local_path = Path(local_path)

        if not from_path:
            from_path = ""

        # in this case, we clone to a temporary directory and move the subdirectory over
        tmp_dir = None
        tmp_dir = TemporaryDirectory(suffix="prefect")
        path_to_move = str(Path(tmp_dir.name).joinpath(from_path))
        if from_path:
            destination_path = local_path.joinpath(from_path)
        else:
            destination_path = local_path

        cmd += f" {tmp_dir.name}"

        try:
            err_stream = io.StringIO()
            out_stream = io.StringIO()
            process = await run_process(cmd, stream_output=(out_stream, err_stream))

            # move directory from temp folder to designated directory
            copytree(
                src=path_to_move,
                dst=destination_path,
                copy_function=shutil.move,
                dirs_exist_ok=True,
            )

        except Exception as exc:
            raise exc

        finally:
            if tmp_dir:
                tmp_dir.cleanup()

        if process.returncode != 0:
            err_stream.seek(0)
            raise OSError(f"Failed to pull from remote:\n {err_stream.read()}")


@task
async def query_repository(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    The query root of GitHub's GraphQL interface.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a repository
            referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    )

    op_stack = ("repository",)
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]


@task
async def query_repository_ref(
    owner: str,
    name: str,
    qualified_name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Fetch a given ref from the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        qualified_name: The ref to retrieve. Fully qualified matches are
            checked in order (`refs/heads/master`) before falling back
            onto checks for short name matches (`master`).
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).ref(
        **strip_kwargs(
            qualified_name=qualified_name,
        )
    )

    op_stack = (
        "repository",
        "ref",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["ref"]


@task
async def query_repository_refs(
    owner: str,
    name: str,
    ref_prefix: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    query: str = None,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    direction: graphql_schema.OrderDirection = None,
    order_by: graphql_schema.RefOrder = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Fetch a list of refs from the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        ref_prefix: A ref name prefix like `refs/heads/`, `refs/tags/`,
            etc.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        query: Filters refs with query on name.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before the
            specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        direction: DEPRECATED: use orderBy. The ordering direction.
        order_by: Ordering options for refs returned from the connection.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).refs(
        **strip_kwargs(
            ref_prefix=ref_prefix,
            query=query,
            after=after,
            before=before,
            first=first,
            last=last,
            direction=direction,
            order_by=order_by,
        )
    )

    op_stack = (
        "repository",
        "refs",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["refs"]


@task
async def query_repository_owner(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    The User owner of the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).owner(**strip_kwargs())

    op_stack = (
        "repository",
        "owner",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["owner"]


@task
async def query_repository_forks(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    privacy: graphql_schema.RepositoryPrivacy = None,
    order_by: graphql_schema.RepositoryOrder = None,
    affiliations: Iterable[graphql_schema.RepositoryAffiliation] = None,
    owner_affiliations: Iterable[graphql_schema.RepositoryAffiliation] = [
        "OWNER",
        "COLLABORATOR",
    ],
    is_locked: bool = None,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of direct forked repositories.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        privacy: If non-null, filters repositories according to privacy.
        order_by: Ordering options for repositories returned from the
            connection.
        affiliations: Array of viewer's affiliation options for
            repositories returned from the connection. For example,
            OWNER will include only repositories that the current viewer
            owns.
        owner_affiliations: Array of owner's affiliation options for
            repositories returned from the connection. For example,
            OWNER will include only repositories that the organization
            or user being viewed owns.
        is_locked: If non-null, filters repositories according to whether
            they have been locked.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before the
            specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).forks(
        **strip_kwargs(
            privacy=privacy,
            order_by=order_by,
            affiliations=affiliations,
            owner_affiliations=owner_affiliations,
            is_locked=is_locked,
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "forks",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["forks"]


@task
async def query_repository_issue(
    owner: str,
    name: str,
    number: int,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Returns a single issue from the current repository by number.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        number: The number for the issue to be returned.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).issue(
        **strip_kwargs(
            number=number,
        )
    )

    op_stack = (
        "repository",
        "issue",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["issue"]


@task
async def query_repository_label(
    owner: str,
    name: str,
    label_name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Returns a single label by name.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        label_name: Label name.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).label(
        **strip_kwargs(
            name=label_name,
        )
    )

    op_stack = (
        "repository",
        "label",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["label"]


@task
async def query_repository_issues(
    owner: str,
    name: str,
    labels: Iterable[str],
    states: Iterable[graphql_schema.IssueState],
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    order_by: graphql_schema.IssueOrder = None,
    filter_by: graphql_schema.IssueFilters = None,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of issues that have been opened in the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        labels: A list of label names to filter the pull requests by.
        states: A list of states to filter the issues by.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        order_by: Ordering options for issues returned from the
            connection.
        filter_by: Filtering options for issues returned from the
            connection.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before the
            specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).issues(
        **strip_kwargs(
            labels=labels,
            states=states,
            order_by=order_by,
            filter_by=filter_by,
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "issues",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["issues"]


@task
async def query_repository_labels(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    order_by: graphql_schema.LabelOrder = {"field": "CREATED_AT", "direction": "ASC"},
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    query: str = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of labels associated with the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        order_by: Ordering options for labels returned from the
            connection.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before the
            specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        query: If provided, searches labels by name and description.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).labels(
        **strip_kwargs(
            order_by=order_by,
            after=after,
            before=before,
            first=first,
            last=last,
            query=query,
        )
    )

    op_stack = (
        "repository",
        "labels",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["labels"]


@task
async def query_repository_object(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    oid: datetime = None,
    expression: str = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A Git object in the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        oid: The Git object ID.
        expression: A Git revision expression suitable for rev-parse.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).object(
        **strip_kwargs(
            oid=oid,
            expression=expression,
        )
    )

    op_stack = (
        "repository",
        "object",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["object"]


@task
async def query_repository_project(
    owner: str,
    name: str,
    number: int,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Find project by number.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        number: The project number to find.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).project(
        **strip_kwargs(
            number=number,
        )
    )

    op_stack = (
        "repository",
        "project",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["project"]


@task
async def query_repository_release(
    owner: str,
    name: str,
    tag_name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Lookup a single release given various criteria.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        tag_name: The name of the Tag the Release was created from.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).release(
        **strip_kwargs(
            tag_name=tag_name,
        )
    )

    op_stack = (
        "repository",
        "release",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["release"]


@task
async def query_repository_projects(
    owner: str,
    name: str,
    states: Iterable[graphql_schema.ProjectState],
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    order_by: graphql_schema.ProjectOrder = None,
    search: str = None,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of projects under the owner.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        states: A list of states to filter the projects by.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        order_by: Ordering options for projects returned from the
            connection.
        search: Query to search projects by, currently only searching
            by name.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before the
            specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).projects(
        **strip_kwargs(
            states=states,
            order_by=order_by,
            search=search,
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "projects",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["projects"]


@task
async def query_repository_packages(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    names: Iterable[str] = None,
    repository_id: str = None,
    package_type: graphql_schema.PackageType = None,
    order_by: graphql_schema.PackageOrder = {
        "field": "CREATED_AT",
        "direction": "DESC",
    },
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of packages under the owner.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before the
            specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        names: Find packages by their names.
        repository_id: Find packages in a repository by ID.
        package_type: Filter registry package by type.
        order_by: Ordering of the returned packages.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).packages(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
            names=names,
            repository_id=repository_id,
            package_type=package_type,
            order_by=order_by,
        )
    )

    op_stack = (
        "repository",
        "packages",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["packages"]


@task
async def query_repository_releases(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    order_by: graphql_schema.ReleaseOrder = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    List of releases which are dependent on this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before the
            specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        order_by: Order for connection.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).releases(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
            order_by=order_by,
        )
    )

    op_stack = (
        "repository",
        "releases",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["releases"]


@task
async def query_repository_watchers(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of users watching the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before the
            specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).watchers(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "watchers",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["watchers"]


@task
async def query_repository_languages(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    order_by: graphql_schema.LanguageOrder = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list containing a breakdown of the language composition of the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before the
            specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        order_by: Order for connection.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).languages(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
            order_by=order_by,
        )
    )

    op_stack = (
        "repository",
        "languages",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["languages"]


@task
async def query_repository_milestone(
    owner: str,
    name: str,
    number: int,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Returns a single milestone from the current repository by number.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        number: The number for the milestone to be returned.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).milestone(
        **strip_kwargs(
            number=number,
        )
    )

    op_stack = (
        "repository",
        "milestone",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["milestone"]


@task
async def query_repository_project_v2(
    owner: str,
    name: str,
    number: int,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Finds and returns the Project according to the provided Project number.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        number: The Project number.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).project_v2(
        **strip_kwargs(
            number=number,
        )
    )

    op_stack = (
        "repository",
        "projectV2",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["projectV2"]


@task
async def query_repository_stargazers(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    order_by: graphql_schema.StarOrder = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of users who have starred this starrable.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before the
            specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        order_by: Order for connection.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).stargazers(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
            order_by=order_by,
        )
    )

    op_stack = (
        "repository",
        "stargazers",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["stargazers"]


@task
async def query_repository_deploy_keys(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of deploy keys that are on this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before
            the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).deploy_keys(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "deployKeys",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["deployKeys"]


@task
async def query_repository_discussion(
    owner: str,
    name: str,
    number: int,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Returns a single discussion from the current repository by number.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        number: The number for the discussion to be returned.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).discussion(
        **strip_kwargs(
            number=number,
        )
    )

    op_stack = (
        "repository",
        "discussion",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["discussion"]


@task
async def query_repository_milestones(
    owner: str,
    name: str,
    states: Iterable[graphql_schema.MilestoneState],
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    order_by: graphql_schema.MilestoneOrder = None,
    query: str = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of milestones associated with the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        states: Filter by the state of the milestones.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before the
            specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        order_by: Ordering options for milestones.
        query: Filters milestones with a query on the title.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).milestones(
        **strip_kwargs(
            states=states,
            after=after,
            before=before,
            first=first,
            last=last,
            order_by=order_by,
            query=query,
        )
    )

    op_stack = (
        "repository",
        "milestones",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["milestones"]


@task
async def query_repository_projects_v2(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    query: str = None,
    order_by: graphql_schema.ProjectV2Order = {"field": "NUMBER", "direction": "DESC"},
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    List of projects linked to this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before
            the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        query: A project to search for linked to the repo.
        order_by: How to order the returned projects.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).projects_v2(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
            query=query,
            order_by=order_by,
        )
    )

    op_stack = (
        "repository",
        "projectsV2",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["projectsV2"]


@task
async def query_repository_submodules(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Returns a list of all submodules in this repository parsed from the .gitmodules
    file as of the default branch's HEAD commit.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before the
            specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).submodules(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "submodules",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["submodules"]


@task
async def query_repository_license_info(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    The license associated with the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).license_info(**strip_kwargs())

    op_stack = (
        "repository",
        "licenseInfo",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["licenseInfo"]


@task
async def query_repository_deployments(
    owner: str,
    name: str,
    environments: Iterable[str],
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    order_by: graphql_schema.DeploymentOrder = {
        "field": "CREATED_AT",
        "direction": "ASC",
    },
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Deployments associated with the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        environments: Environments to list deployments for.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        order_by: Ordering options for deployments returned from the
            connection.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before
            the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).deployments(
        **strip_kwargs(
            environments=environments,
            order_by=order_by,
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "deployments",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["deployments"]


@task
async def query_repository_discussions(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    category_id: str = None,
    order_by: graphql_schema.DiscussionOrder = {
        "field": "UPDATED_AT",
        "direction": "DESC",
    },
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of discussions that have been opened in the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before
            the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        category_id: Only include discussions that belong to the
            category with this ID.
        order_by: Ordering options for discussions returned from the
            connection.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).discussions(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
            category_id=category_id,
            order_by=order_by,
        )
    )

    op_stack = (
        "repository",
        "discussions",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["discussions"]


@task
async def query_repository_environment(
    owner: str,
    name: str,
    environment_name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Returns a single active environment from the current repository by name.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        environment_name: The name of the environment to be returned.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).environment(
        **strip_kwargs(
            name=environment_name,
        )
    )

    op_stack = (
        "repository",
        "environment",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["environment"]


@task
async def query_repository_project_next(
    owner: str,
    name: str,
    number: int,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Finds and returns the Project (beta) according to the provided Project (beta)
    number.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        number: The ProjectNext number.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).project_next(
        **strip_kwargs(
            number=number,
        )
    )

    op_stack = (
        "repository",
        "projectNext",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["projectNext"]


@task
async def query_repository_pull_request(
    owner: str,
    name: str,
    number: int,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Returns a single pull request from the current repository by number.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        number: The number for the pull request to be returned.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).pull_request(
        **strip_kwargs(
            number=number,
        )
    )

    op_stack = (
        "repository",
        "pullRequest",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["pullRequest"]


@task
async def query_repository_contact_links(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Returns a list of contact links associated to the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).contact_links(**strip_kwargs())

    op_stack = (
        "repository",
        "contactLinks",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["contactLinks"]


@task
async def query_repository_environments(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of environments that are in this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after the
            specified cursor.
        before: Returns the elements in the list that come before
            the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).environments(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "environments",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["environments"]


@task
async def query_repository_funding_links(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    The funding links for this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).funding_links(**strip_kwargs())

    op_stack = (
        "repository",
        "fundingLinks",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["fundingLinks"]


@task
async def query_repository_pinned_issues(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of pinned issues for this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after
            the specified cursor.
        before: Returns the elements in the list that come before
            the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).pinned_issues(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "pinnedIssues",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["pinnedIssues"]


@task
async def query_repository_projects_next(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    query: str = None,
    sort_by: graphql_schema.ProjectNextOrderField = "TITLE",
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    List of projects (beta) linked to this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after
            the specified cursor.
        before: Returns the elements in the list that come before
            the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        query: A project (beta) to search for linked to the repo.
        sort_by: How to order the returned project (beta) objects.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).projects_next(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
            query=query,
            sort_by=sort_by,
        )
    )

    op_stack = (
        "repository",
        "projectsNext",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["projectsNext"]


@task
async def query_repository_pull_requests(
    owner: str,
    name: str,
    states: Iterable[graphql_schema.PullRequestState],
    labels: Iterable[str],
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    head_ref_name: str = None,
    base_ref_name: str = None,
    order_by: graphql_schema.IssueOrder = None,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of pull requests that have been opened in the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        states: A list of states to filter the pull requests by.
        labels: A list of label names to filter the pull requests
            by.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        head_ref_name: The head ref name to filter the pull
            requests by.
        base_ref_name: The base ref name to filter the pull
            requests by.
        order_by: Ordering options for pull requests returned from
            the connection.
        after: Returns the elements in the list that come after
            the specified cursor.
        before: Returns the elements in the list that come before
            the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).pull_requests(
        **strip_kwargs(
            states=states,
            labels=labels,
            head_ref_name=head_ref_name,
            base_ref_name=base_ref_name,
            order_by=order_by,
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "pullRequests",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["pullRequests"]


@task
async def query_repository_code_of_conduct(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Returns the code of conduct for this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).code_of_conduct(**strip_kwargs())

    op_stack = (
        "repository",
        "codeOfConduct",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["codeOfConduct"]


@task
async def query_repository_collaborators(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    affiliation: graphql_schema.CollaboratorAffiliation = None,
    query: str = None,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of collaborators associated with the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        affiliation: Collaborators affiliation level with a
            repository.
        query: Filters users with query on user name and login.
        after: Returns the elements in the list that come after
            the specified cursor.
        before: Returns the elements in the list that come before
            the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).collaborators(
        **strip_kwargs(
            affiliation=affiliation,
            query=query,
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "collaborators",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["collaborators"]


@task
async def query_repository_latest_release(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Get the latest release for the repository if one exists.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).latest_release(**strip_kwargs())

    op_stack = (
        "repository",
        "latestRelease",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["latestRelease"]


@task
async def query_repository_recent_projects(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Recent projects that this user has modified in the context of the owner.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after
            the specified cursor.
        before: Returns the elements in the list that come
            before the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).recent_projects(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "recentProjects",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["recentProjects"]


@task
async def query_repository_commit_comments(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of commit comments associated with the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come after
            the specified cursor.
        before: Returns the elements in the list that come
            before the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).commit_comments(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "commitComments",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["commitComments"]


@task
async def query_repository_issue_templates(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Returns a list of issue templates associated to the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).issue_templates(**strip_kwargs())

    op_stack = (
        "repository",
        "issueTemplates",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["issueTemplates"]


@task
async def query_repository_assignable_users(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    query: str = None,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of users that can be assigned to issues in this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        query: Filters users with query on user name and login.
        after: Returns the elements in the list that come after
            the specified cursor.
        before: Returns the elements in the list that come
            before the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).assignable_users(
        **strip_kwargs(
            query=query,
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "assignableUsers",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["assignableUsers"]


@task
async def query_repository_primary_language(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    The primary language of the repository's code.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).primary_language(**strip_kwargs())

    op_stack = (
        "repository",
        "primaryLanguage",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["primaryLanguage"]


@task
async def query_repository_default_branch_ref(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    The Ref associated with the repository's default branch.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).default_branch_ref(**strip_kwargs())

    op_stack = (
        "repository",
        "defaultBranchRef",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["defaultBranchRef"]


@task
async def query_repository_mentionable_users(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    query: str = None,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of Users that can be mentioned in the context of the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        query: Filters users with query on user name and login.
        after: Returns the elements in the list that come
            after the specified cursor.
        before: Returns the elements in the list that come
            before the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).mentionable_users(
        **strip_kwargs(
            query=query,
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "mentionableUsers",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["mentionableUsers"]


@task
async def query_repository_repository_topics(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of applied repository-topic associations for this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come
            after the specified cursor.
        before: Returns the elements in the list that come
            before the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).repository_topics(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "repositoryTopics",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["repositoryTopics"]


@task
async def query_repository_pinned_discussions(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of discussions that have been pinned in this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come
            after the specified cursor.
        before: Returns the elements in the list that come
            before the specified cursor.
        first: Returns the first _n_ elements from the list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).pinned_discussions(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "pinnedDiscussions",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["pinnedDiscussions"]


@task
async def query_repository_discussion_category(
    owner: str,
    name: str,
    slug: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A discussion category by slug.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        slug: The slug of the discussion category to be
            returned.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).discussion_category(
        **strip_kwargs(
            slug=slug,
        )
    )

    op_stack = (
        "repository",
        "discussionCategory",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["discussionCategory"]


@task
async def query_repository_interaction_ability(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    The interaction ability settings for this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).interaction_ability(**strip_kwargs())

    op_stack = (
        "repository",
        "interactionAbility",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["interactionAbility"]


@task
async def query_repository_issue_or_pull_request(
    owner: str,
    name: str,
    number: int,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Returns a single issue-like object from the current repository by number.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        number: The number for the issue to be returned.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).issue_or_pull_request(
        **strip_kwargs(
            number=number,
        )
    )

    op_stack = (
        "repository",
        "issueOrPullRequest",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["issueOrPullRequest"]


@task
async def query_repository_vulnerability_alerts(
    owner: str,
    name: str,
    states: Iterable[graphql_schema.RepositoryVulnerabilityAlertState],
    dependency_scopes: Iterable[
        graphql_schema.RepositoryVulnerabilityAlertDependencyScope
    ],
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of vulnerability alerts that are on this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        states: Filter by the state of the alert.
        dependency_scopes: Filter by the scope of the
            alert's dependency.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come
            after the specified cursor.
        before: Returns the elements in the list that come
            before the specified cursor.
        first: Returns the first _n_ elements from the
            list.
        last: Returns the last _n_ elements from the list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).vulnerability_alerts(
        **strip_kwargs(
            states=states,
            dependency_scopes=dependency_scopes,
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "vulnerabilityAlerts",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["vulnerabilityAlerts"]


@task
async def query_repository_discussion_categories(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    filter_by_assignable: bool = False,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of discussion categories that are available in the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that come
            after the specified cursor.
        before: Returns the elements in the list that come
            before the specified cursor.
        first: Returns the first _n_ elements from the
            list.
        last: Returns the last _n_ elements from the list.
        filter_by_assignable: Filter by categories that
            are assignable by the viewer.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).discussion_categories(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
            filter_by_assignable=filter_by_assignable,
        )
    )

    op_stack = (
        "repository",
        "discussionCategories",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["discussionCategories"]


@task
async def query_repository_pull_request_templates(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    Returns a list of pull request templates associated to the repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).pull_request_templates(**strip_kwargs())

    op_stack = (
        "repository",
        "pullRequestTemplates",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["pullRequestTemplates"]


@task
async def query_repository_branch_protection_rules(
    owner: str,
    name: str,
    github_credentials: GitHubCredentials,
    follow_renames: bool = True,
    after: str = None,
    before: str = None,
    first: int = None,
    last: int = None,
    return_fields: Iterable[str] = None,
) -> Dict[str, Any]:
    """
    A list of branch protection rules for this repository.

    Args:
        owner: The login field of a user or organization.
        name: The name of the repository.
        github_credentials: Credentials to use for authentication with GitHub.
        follow_renames: Follow repository renames. If disabled, a
            repository referenced by its old name will return an error.
        after: Returns the elements in the list that
            come after the specified cursor.
        before: Returns the elements in the list that
            come before the specified cursor.
        first: Returns the first _n_ elements from the
            list.
        last: Returns the last _n_ elements from the
            list.
        return_fields: Subset the return fields (as snake_case); defaults to
            fields listed in configs/query/*.json.

    Returns:
        A dict of the returned fields.
    """
    op = Operation(graphql_schema.Query)
    op_selection = op.repository(
        **strip_kwargs(
            owner=owner,
            name=name,
            follow_renames=follow_renames,
        )
    ).branch_protection_rules(
        **strip_kwargs(
            after=after,
            before=before,
            first=first,
            last=last,
        )
    )

    op_stack = (
        "repository",
        "branchProtectionRules",
    )
    op_selection = _subset_return_fields(
        op_selection, op_stack, return_fields, return_fields_defaults
    )

    result = await _execute_graphql_op(op, github_credentials)
    return result["repository"]["branchProtectionRules"]
