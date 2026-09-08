@workflow @definitions
Feature: Workflow Definitions

  Scenario: Valid minimal workflow definition
    Given a minimal workflow with name test-wf and empty steps
    When I validate the workflow
    Then validation should pass

  Scenario: Workflow missing name is rejected
    Given a workflow with missing name
    When I validate the workflow
    Then validation should fail with name

  Scenario: Workflow missing steps is rejected
    Given a workflow with missing steps
    When I validate the workflow
    Then validation should fail with steps

  Scenario: Step missing name is rejected
    Given a workflow with a step missing name
    When I validate the workflow
    Then validation should fail with missing name

  Scenario: Invalid step type is rejected
    Given a workflow with step type bad_type
    When I validate the workflow
    Then validation should fail with invalid type

  Scenario: Permission allowed_tools must be list
    Given a workflow with permission allowed_tools as string
    When I validate the workflow
    Then validation should fail with allowed_tools

  Scenario: Parallel branch missing branches is rejected
    Given a workflow with parallel_branch missing branches
    When I validate the workflow
    Then validation should fail with parallel_branch requires branches

  Scenario: Parallel branch branch missing name is rejected
    Given a workflow with parallel_branch branch missing name
    When I validate the workflow
    Then validation should fail with branch missing name

  Scenario: Workflow YAML parse and dump roundtrip
    Given a valid workflow YAML
    When I parse it to dict and dump back to YAML
    Then the result should be valid workflow YAML
