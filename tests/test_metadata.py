"""
metadata.py のテスト

SF CLI への呼び出しは unittest.mock でモックし、外部依存なしでテストする。
"""

import json
from unittest.mock import MagicMock, patch

from sf_package_xml.metadata import get_metadata_types, get_org_api_version, run_sf


def _sf_run(stdout_obj):
    """subprocess.run の戻り値を模したモックを作る。"""
    return MagicMock(stdout=json.dumps(stdout_obj), stderr="")


class TestRunSf:
    def test_returns_data_on_success(self):
        with patch("sf_package_xml.metadata.subprocess.run", return_value=_sf_run({"status": 0, "result": []})):
            assert run_sf(["org", "list", "metadata", "-m", "ApexClass"]) == {"status": 0, "result": []}

    def test_returns_none_on_json_decode_error(self):
        mock = MagicMock(stdout="not json", stderr="")
        with patch("sf_package_xml.metadata.subprocess.run", return_value=mock):
            assert run_sf(["org", "list", "metadata", "-m", "ApexClass"]) is None

    def test_retries_and_recovers_on_throttle_error(self):
        throttled = _sf_run({"status": 1, "name": "REQUEST_LIMIT_EXCEEDED", "message": "REQUEST_LIMIT_EXCEEDED"})
        recovered = _sf_run({"status": 0, "result": ["Foo"]})
        with patch("sf_package_xml.metadata.subprocess.run", side_effect=[throttled, recovered]), \
             patch("sf_package_xml.metadata.time.sleep"):
            assert run_sf(["org", "list", "metadata", "-m", "ApexClass"]) == {"status": 0, "result": ["Foo"]}

    def test_returns_none_when_result_missing_after_retries_exhausted(self):
        # status != 0 かつ result 欠損のレスポンスが max_retries 回続く場合、
        # 「result欠損=呼び出し失敗」として None を返す (0件と誤認させない)。
        broken = _sf_run({"status": 1, "message": "Unexpected error"})
        with patch("sf_package_xml.metadata.subprocess.run", return_value=broken), \
             patch("sf_package_xml.metadata.time.sleep"):
            assert run_sf(["org", "list", "metadata", "-m", "WaveDashboard"], max_retries=2) is None

    def test_recovers_when_result_missing_response_is_transient(self):
        # result欠損レスポンスが1回だけ発生し、リトライで正常なレスポンスに
        # 復帰するケース (高並列実行時に稀に発生する想定のシナリオ)。
        broken = _sf_run({"status": 1, "message": "Unexpected error"})
        recovered = _sf_run({"status": 0, "result": ["WaveDashboard1"]})
        with patch("sf_package_xml.metadata.subprocess.run", side_effect=[broken, recovered]), \
             patch("sf_package_xml.metadata.time.sleep"):
            assert run_sf(["org", "list", "metadata", "-m", "WaveDashboard"]) == {
                "status": 0, "result": ["WaveDashboard1"],
            }

    def test_returns_data_when_status_nonzero_but_result_present(self):
        # result が存在する「警告付き成功」はリトライせずそのまま返す
        response = {"status": 1, "message": "some warning", "result": ["Foo"]}
        with patch("sf_package_xml.metadata.subprocess.run", return_value=_sf_run(response)):
            assert run_sf(["org", "list", "metadata", "-m", "ApexClass"]) == response


class TestGetOrgApiVersion:
    def test_returns_version_from_api_version_field(self):
        response = {"status": 0, "result": {"apiVersion": "66.0"}}
        with patch("sf_package_xml.metadata.run_sf", return_value=response):
            assert get_org_api_version(None) == "66.0"

    def test_returns_version_from_instance_api_version_field(self):
        response = {"status": 0, "result": {"instanceApiVersion": "63.0"}}
        with patch("sf_package_xml.metadata.run_sf", return_value=response):
            assert get_org_api_version(None) == "63.0"

    def test_api_version_takes_precedence_over_instance_api_version(self):
        response = {"status": 0, "result": {"apiVersion": "66.0", "instanceApiVersion": "63.0"}}
        with patch("sf_package_xml.metadata.run_sf", return_value=response):
            assert get_org_api_version(None) == "66.0"

    def test_returns_version_with_target_org(self):
        response = {"status": 0, "result": {"apiVersion": "62.0"}}
        with patch("sf_package_xml.metadata.run_sf", return_value=response):
            assert get_org_api_version("myOrg") == "62.0"

    def test_returns_none_when_run_sf_fails(self):
        with patch("sf_package_xml.metadata.run_sf", return_value=None):
            assert get_org_api_version(None) is None

    def test_returns_none_when_result_missing(self):
        with patch("sf_package_xml.metadata.run_sf", return_value={"status": 0, "result": {}}):
            assert get_org_api_version(None) is None

    def test_returns_none_when_version_empty_string(self):
        response = {"status": 0, "result": {"apiVersion": ""}}
        with patch("sf_package_xml.metadata.run_sf", return_value=response):
            assert get_org_api_version(None) is None

    def test_returns_none_when_version_is_not_string(self):
        response = {"status": 0, "result": {"apiVersion": 66}}
        with patch("sf_package_xml.metadata.run_sf", return_value=response):
            assert get_org_api_version(None) is None

    def test_falls_back_to_instance_api_version_when_api_version_is_int(self):
        # apiVersion が int の場合は instanceApiVersion にフォールバックする
        response = {"status": 0, "result": {"apiVersion": 66, "instanceApiVersion": "63.0"}}
        with patch("sf_package_xml.metadata.run_sf", return_value=response):
            assert get_org_api_version(None) == "63.0"

    def test_returns_none_when_result_is_null(self):
        # result キーが存在するが値が null の場合
        response = {"status": 0, "result": None}
        with patch("sf_package_xml.metadata.run_sf", return_value=response):
            assert get_org_api_version(None) is None


class TestGetMetadataTypes:
    def test_returns_types_when_all_calls_agree(self):
        objects = [{"xmlName": "ApexClass"}, {"xmlName": "CustomObject"}]
        response = {"status": 0, "result": {"metadataObjects": objects}}
        with patch("sf_package_xml.metadata.run_sf", return_value=response):
            result = get_metadata_types(None)
        assert {t["xmlName"] for t in result} == {"ApexClass", "CustomObject"}

    def test_merges_union_when_counts_disagree_between_calls(self):
        # 1回目は WaveDashboard が欠落した不完全なリスト、2回目は完全なリスト。
        # 実際の org で "sf org list metadata-types" の件数が呼び出しごとに
        # 揺れる (235件 / 246件) ことが確認されたため、和集合を取る。
        incomplete = {"status": 0, "result": {"metadataObjects": [{"xmlName": "ApexClass"}]}}
        complete = {
            "status": 0,
            "result": {"metadataObjects": [{"xmlName": "ApexClass"}, {"xmlName": "WaveDashboard"}]},
        }
        with patch("sf_package_xml.metadata.run_sf", side_effect=[incomplete, complete]):
            result = get_metadata_types(None)
        assert {t["xmlName"] for t in result} == {"ApexClass", "WaveDashboard"}

    def test_recovers_when_first_call_fails_entirely(self):
        complete = {"status": 0, "result": {"metadataObjects": [{"xmlName": "ApexClass"}]}}
        with patch("sf_package_xml.metadata.run_sf", side_effect=[None, complete]):
            result = get_metadata_types(None)
        assert {t["xmlName"] for t in result} == {"ApexClass"}

    def test_returns_empty_list_when_all_attempts_fail(self):
        with patch("sf_package_xml.metadata.run_sf", return_value=None):
            assert get_metadata_types(None) == []

    def test_calls_run_sf_attempts_times(self):
        response = {"status": 0, "result": {"metadataObjects": [{"xmlName": "ApexClass"}]}}
        with patch("sf_package_xml.metadata.run_sf", return_value=response) as mock_run_sf:
            get_metadata_types(None, attempts=3)
        assert mock_run_sf.call_count == 3

    def test_single_attempt_when_attempts_is_one(self):
        response = {"status": 0, "result": {"metadataObjects": [{"xmlName": "ApexClass"}]}}
        with patch("sf_package_xml.metadata.run_sf", return_value=response) as mock_run_sf:
            result = get_metadata_types(None, attempts=1)
        assert mock_run_sf.call_count == 1
        assert {t["xmlName"] for t in result} == {"ApexClass"}
