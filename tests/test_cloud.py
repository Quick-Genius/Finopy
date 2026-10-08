"""Tests for cloud provider integrations (mocked)."""

import pytest
from unittest.mock import patch, MagicMock


class TestCostExplorer:
    @patch("cloud.aws.cost_explorer.boto3")
    @pytest.mark.asyncio
    async def test_fetch_cost_and_usage(self, mock_boto3):
        """Should normalize AWS Cost Explorer response."""
        from cloud.aws.cost_explorer import fetch_cost_and_usage

        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.get_cost_and_usage.return_value = {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2024-01-01", "End": "2024-01-02"},
                    "Groups": [
                        {
                            "Keys": ["Amazon EC2", "us-east-1"],
                            "Metrics": {
                                "UnblendedCost": {"Amount": "150.00", "Unit": "USD"},
                                "UsageQuantity": {"Amount": "720", "Unit": "Hours"},
                            },
                        }
                    ],
                }
            ]
        }

        result = await fetch_cost_and_usage("2024-01-01", "2024-01-02")

        assert "results_by_time" in result
        assert len(result["results_by_time"]) == 1
        assert result["results_by_time"][0]["groups"][0]["keys"] == ["Amazon EC2", "us-east-1"]

    @patch("cloud.aws.cost_explorer.boto3")
    @pytest.mark.asyncio
    async def test_get_rightsizing_recommendations(self, mock_boto3):
        """Should parse rightsizing recommendations."""
        from cloud.aws.cost_explorer import get_rightsizing_recommendations

        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.get_rightsizing_recommendation.return_value = {
            "RightsizingRecommendations": [
                {
                    "CurrentInstance": {
                        "ResourceId": "i-abc123",
                        "ResourceDetails": {
                            "EC2ResourceDetails": {"InstanceType": "m5.xlarge"}
                        },
                    },
                    "ModifyRecommendationDetail": {
                        "TargetInstances": [
                            {
                                "ResourceDetails": {
                                    "EC2ResourceDetails": {"InstanceType": "m5.large"}
                                },
                                "EstimatedMonthlySavings": {"Value": "45.00"},
                            }
                        ]
                    },
                }
            ]
        }

        result = await get_rightsizing_recommendations()

        assert len(result) == 1
        assert result[0]["instance_id"] == "i-abc123"
        assert result[0]["recommended_type"] == "m5.large"
        assert result[0]["estimated_savings"] == 45.0


class TestCloudWatch:
    @patch("cloud.aws.cloudwatch.boto3")
    @pytest.mark.asyncio
    async def test_get_ec2_utilization(self, mock_boto3):
        """Should classify idle and oversized instances."""
        from cloud.aws.cloudwatch import get_ec2_utilization

        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.get_metric_statistics.return_value = {
            "Datapoints": [
                {"Average": 3.0, "Maximum": 8.0},
                {"Average": 2.0, "Maximum": 5.0},
            ]
        }

        result = await get_ec2_utilization(["i-idle123"])

        assert "i-idle123" in result
        assert result["i-idle123"]["is_idle"] is True
        assert result["i-idle123"]["avg_cpu_pct"] == 2.5


class TestEC2Advisor:
    @patch("cloud.aws.ec2_advisor._get_ec2_client")
    @patch("cloud.aws.ec2_advisor.get_ec2_utilization")
    @pytest.mark.asyncio
    async def test_find_unattached_ebs_volumes(self, mock_util, mock_ec2):
        """Should find and price unattached volumes."""
        from cloud.aws.ec2_advisor import find_unattached_ebs_volumes

        mock_client = MagicMock()
        mock_ec2.return_value = mock_client
        mock_client.describe_volumes.return_value = {
            "Volumes": [
                {
                    "VolumeId": "vol-123",
                    "Size": 100,
                    "VolumeType": "gp3",
                    "CreateTime": "2024-01-01T00:00:00Z",
                }
            ]
        }

        result = await find_unattached_ebs_volumes()

        assert len(result) == 1
        assert result[0]["volume_id"] == "vol-123"
        assert result[0]["monthly_cost_estimate"] == 8.0  # 100GB * $0.08
