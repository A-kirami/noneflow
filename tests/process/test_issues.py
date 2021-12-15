# type: ignore
import json
from pathlib import Path
from typing import Any

from github.Repository import Repository
from pytest_mock import MockerFixture

from src.models import Config, Settings
from src.process import process_issues_event


def mocked_requests_get(url: str):
    class MockResponse:
        def __init__(self, status_code: int):
            self.status_code = status_code

    if url == "https://v2.nonebot.dev":
        return MockResponse(200)

    return MockResponse(404)


def check_json_data(file: Path, data: Any) -> None:
    with open(file, "r") as f:
        assert json.load(f) == data


def test_process_issues(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch("requests.get", side_effect=mocked_requests_get)
    mock_subprocess_run = mocker.patch("subprocess.run")
    mock_settings: Settings = mocker.MagicMock()
    mock_repo: Repository = mocker.MagicMock()

    mock_repo.get_issue().title = "Bot: test"
    mock_repo.get_issue().number = 1
    mock_repo.get_issue().body = """
        <!-- DO NOT EDIT ! -->
        <!--
        - name: test
        - desc: desc
        - homepage: https://v2.nonebot.dev
        - tags: tag
        - is_official: false
        -->
        """
    mock_repo.get_issue().user.login = "test"
    mock_comment = mocker.MagicMock()
    mock_comment.body = "Bot: test"
    mock_repo.get_issue().get_comments.return_value = [mock_comment]

    with open(tmp_path / "bot.json", "w") as f:
        json.dump([], f)

    with open(tmp_path / "event.json", "w") as f:
        json.dump(
            {
                "action": "opened",
                "issue": {
                    "number": 1,
                },
            },
            f,
        )

    mock_settings.github_event_path = str(tmp_path / "event.json")
    mock_settings.input_config = Config(
        base="master",
        plugin_path=tmp_path / "plugin.json",
        bot_path=tmp_path / "bot.json",
        adapter_path=tmp_path / "adapter.json",
    )

    check_json_data(mock_settings.input_config.bot_path, [])

    process_issues_event(mock_settings, mock_repo)

    mock_repo.get_issue.assert_called_with(1)
    # 测试自动添加标签
    mock_repo.get_issue().edit.assert_called_with(labels=["Bot"])

    # 测试 git 命令
    mock_subprocess_run.assert_has_calls(
        [
            mocker.call(["git", "switch", "-C", "publish/issue1"], check=True),
            mocker.call(["git", "config", "--global", "user.name", "test"], check=True),
            mocker.call(
                [
                    "git",
                    "config",
                    "--global",
                    "user.email",
                    "test@users.noreply.github.com",
                ],
                check=True,
            ),
            mocker.call(["git", "add", "-A"], check=True),
            mocker.call(
                ["git", "commit", "-m", ":beers: publish bot test"], check=True
            ),
            mocker.call(["git", "push", "origin", "publish/issue1", "-f"], check=True),
        ]
    )

    # 检查文件是否正确
    check_json_data(
        mock_settings.input_config.bot_path,
        [
            {
                "name": "test",
                "desc": "desc",
                "author": "test",
                "homepage": "https://v2.nonebot.dev",
                "tags": ["tag"],
                "is_official": False,
            }
        ],
    )

    # 检查是否创建了拉取请求
    mock_repo.create_pull.assert_called_with(
        title="Bot test",
        body="resolve #1",
        base="master",
        head="publish/issue1",
    )
    mock_repo.create_pull().add_to_labels.assert_called_with("Bot")

    # 检查是否创建了评论
    mock_repo.get_issue().create_comment.assert_called_with(
        """# 📃 Publish Check Result\n\n> Bot: test\n\n**✅ All tests passed, you are ready to go!**\n\n<details><summary>Report Detail</summary><pre><code><li>✅ Project <a href="https://v2.nonebot.dev">homepage</a> returns 200.</li></code></pre></details>\n\n---\n\n💪 Powered by NoneBot2 Publish Bot\n"""
    )
