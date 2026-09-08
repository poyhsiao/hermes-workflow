@workflow @execution
Feature: Workflow Execution

  Scenario: Execute single step workflow
    Given a workflow with one tool step that echoes hello
    And an execution store
    When I execute the workflow
    Then the execution should complete with status completed
    And step dummy_step should have result containing hello

  Scenario: Execute parallel branch workflow
    Given a workflow with parallel branches
    And an execution store
    When I execute the workflow with concurrency parallel
    Then the execution should complete with status completed

  Scenario: Execute sequential workflow
    Given a workflow with two sequential steps
    And an execution store
    When I execute the workflow
    Then step step2 should execute after step1 completes

  Scenario: Fail fast on step error
    Given a workflow with error_policy fail_fast and a failing step
    And an execution store
    When I execute the workflow
    Then the execution should complete with status failed
    And remaining steps should not execute

  Scenario: Rollback on failure
    Given a workflow with rollback_policy checkpoint and a failing step
    And an execution store
    When I execute the workflow
    Then the execution should complete with status failed
    And the state should be restored to checkpoint
