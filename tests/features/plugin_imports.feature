@workflow @plugin
Feature: Plugin Configuration

  Scenario: Plugin version is consistent across __init__.py and plugin.yaml
    Given the plugin __init__.py at hermes_dynamic_workflow
    And the plugin.yaml at hermes_dynamic_workflow
    Then the version in __init__.py should match version in plugin.yaml

  Scenario: tools module exports workflow_tools functions
    Given the tools module at hermes_dynamic_workflow
    Then it should export workflow_tools
