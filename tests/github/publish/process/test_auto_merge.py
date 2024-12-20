from nonebot.adapters.github import PullRequestReviewSubmitted
from nonebug import App
from pytest_mock import MockerFixture

from tests.github.event import get_mock_event
from tests.github.utils import get_github_bot


async def test_auto_merge(app: App, mocker: MockerFixture, mock_installation) -> None:
    """测试审查后自动合并

    可直接合并的情况
    """
    mock_subprocess_run = mocker.patch("subprocess.run")

    mock_pull = mocker.MagicMock()
    mock_pull.mergeable = True
    mock_pull_resp = mocker.MagicMock()
    mock_pull_resp.parsed_data = mock_pull

    async with app.test_matcher() as ctx:
        adapter, bot = get_github_bot(ctx)
        event = get_mock_event(PullRequestReviewSubmitted)

        ctx.should_call_api(
            "rest.apps.async_get_repo_installation",
            {"owner": "he0119", "repo": "action-test"},
            mock_installation,
        )
        ctx.should_call_api(
            "rest.pulls.async_get",
            {"owner": "he0119", "repo": "action-test", "pull_number": 100},
            mock_pull_resp,
        )
        ctx.should_call_api(
            "rest.pulls.async_merge",
            {
                "owner": "he0119",
                "repo": "action-test",
                "pull_number": 100,
                "merge_method": "rebase",
            },
            True,
        )

        ctx.receive_event(bot, event)

    # 测试 git 命令
    mock_subprocess_run.assert_has_calls(
        [
            mocker.call(
                ["git", "config", "--global", "safe.directory", "*"],
                check=True,
                capture_output=True,
            ),
            mocker.call(
                ["pre-commit", "install", "--install-hooks"],
                check=True,
                capture_output=True,
            ),
        ],  # type: ignore
        any_order=True,
    )


async def test_auto_merge_need_rebase(
    app: App, mocker: MockerFixture, mock_installation
) -> None:
    """测试审查后自动合并

    需要 rebase 的情况
    """
    from src.plugins.github.models import GithubHandler, RepoInfo

    mock_subprocess_run = mocker.patch("subprocess.run")
    mock_resolve_conflict_pull_requests = mocker.patch(
        "src.plugins.github.plugins.publish.resolve_conflict_pull_requests"
    )

    mock_pull = mocker.MagicMock()
    mock_pull.mergeable = False
    mock_pull.head.ref = "publish/issue1"
    mock_pull_resp = mocker.MagicMock()
    mock_pull_resp.parsed_data = mock_pull

    async with app.test_matcher() as ctx:
        adapter, bot = get_github_bot(ctx)
        event = get_mock_event(PullRequestReviewSubmitted)

        ctx.should_call_api(
            "rest.apps.async_get_repo_installation",
            {"owner": "he0119", "repo": "action-test"},
            mock_installation,
        )
        ctx.should_call_api(
            "rest.pulls.async_get",
            {"owner": "he0119", "repo": "action-test", "pull_number": 100},
            mock_pull_resp,
        )

        ctx.should_call_api(
            "rest.pulls.async_merge",
            {
                "owner": "he0119",
                "repo": "action-test",
                "pull_number": 100,
                "merge_method": "rebase",
            },
            True,
        )

        ctx.receive_event(bot, event)

    # 测试 git 命令
    mock_subprocess_run.assert_has_calls(
        [
            mocker.call(
                ["git", "config", "--global", "safe.directory", "*"],
                check=True,
                capture_output=True,
            ),  # type: ignore
            mocker.call(
                ["pre-commit", "install", "--install-hooks"],
                check=True,
                capture_output=True,
            ),  # type: ignore
        ],
        any_order=True,
    )
    mock_resolve_conflict_pull_requests.assert_called_once_with(
        GithubHandler(bot=bot, repo_info=RepoInfo(owner="he0119", repo="action-test")),
        [mock_pull],
    )


async def test_auto_merge_not_publish(app: App, mocker: MockerFixture) -> None:
    """测试审查后自动合并

    和发布无关
    """
    mock_subprocess_run = mocker.patch("subprocess.run")
    mock_resolve_conflict_pull_requests = mocker.patch(
        "src.plugins.github.plugins.publish.resolve_conflict_pull_requests"
    )

    async with app.test_matcher() as ctx:
        adapter, bot = get_github_bot(ctx)
        event = get_mock_event(PullRequestReviewSubmitted)
        event.payload.pull_request.labels = []

        ctx.receive_event(bot, event)

    # 测试 git 命令
    mock_subprocess_run.assert_not_called()
    mock_resolve_conflict_pull_requests.assert_not_called()


async def test_auto_merge_not_member(app: App, mocker: MockerFixture) -> None:
    """测试审查后自动合并

    审核者不是仓库成员
    """
    mock_subprocess_run = mocker.patch("subprocess.run")
    mock_resolve_conflict_pull_requests = mocker.patch(
        "src.plugins.github.plugins.publish.resolve_conflict_pull_requests"
    )

    async with app.test_matcher() as ctx:
        adapter, bot = get_github_bot(ctx)
        event = get_mock_event(PullRequestReviewSubmitted)
        event.payload.review.author_association = "CONTRIBUTOR"
        ctx.receive_event(bot, event)

    # 测试 git 命令
    mock_subprocess_run.assert_not_called()
    mock_resolve_conflict_pull_requests.assert_not_called()


async def test_auto_merge_not_approve(app: App, mocker: MockerFixture) -> None:
    """测试审查后自动合并

    审核未通过
    """
    mock_subprocess_run = mocker.patch("subprocess.run")
    mock_resolve_conflict_pull_requests = mocker.patch(
        "src.plugins.github.plugins.publish.resolve_conflict_pull_requests"
    )

    async with app.test_matcher() as ctx:
        adapter, bot = get_github_bot(ctx)
        event = get_mock_event(PullRequestReviewSubmitted)
        event.payload.review.state = "commented"

        ctx.receive_event(bot, event)

    # 测试 git 命令
    mock_subprocess_run.assert_not_called()
    mock_resolve_conflict_pull_requests.assert_not_called()
