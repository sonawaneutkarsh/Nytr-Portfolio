import Foundation

// MARK: - POST /v1/training/hevy/sync

struct HevySyncResponse: Codable, Equatable, Sendable {
    let status: String
    let mode: String
    let sessionsCreated: Int
    let revisionsAppended: Int
    let deletionsRecorded: Int
    let hasMore: Bool
    let checkpointAdvanced: Bool

    private enum CodingKeys: String, CodingKey {
        case status
        case mode
        case sessionsCreated = "sessions_created"
        case revisionsAppended = "revisions_appended"
        case deletionsRecorded = "deletions_recorded"
        case hasMore = "has_more"
        case checkpointAdvanced = "checkpoint_advanced"
    }
}

// MARK: - GET /v1/training/analytics/recent

struct TrainingAnalyticsRecentResponse: Codable, Equatable, Sendable {
    let policyVersion: String
    let sessions: [TrainingSessionAnalyticsDTO]

    private enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case sessions
    }
}

struct TrainingSessionAnalyticsDTO: Codable, Equatable, Sendable, Identifiable {
    let revisionId: String
    let sourceSystem: String
    let sourceSessionId: String
    let sourceRevision: String
    let title: String
    let startedAt: Date
    let endedAt: Date
    let sessionDurationSeconds: String
    let exerciseCount: Int
    let recordedSetCount: Int
    let workingSetCount: Int
    let warmupSetCount: Int
    let unsupportedSetCount: Int
    let repTotal: Int?
    let volumeKgReps: String?
    let volumeExerciseCount: Int
    let exercises: [ExerciseOccurrenceAnalyticsDTO]

    var id: String { revisionId }

    private enum CodingKeys: String, CodingKey {
        case revisionId = "revision_id"
        case sourceSystem = "source_system"
        case sourceSessionId = "source_session_id"
        case sourceRevision = "source_revision"
        case title
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case sessionDurationSeconds = "session_duration_seconds"
        case exerciseCount = "exercise_count"
        case recordedSetCount = "recorded_set_count"
        case workingSetCount = "working_set_count"
        case warmupSetCount = "warmup_set_count"
        case unsupportedSetCount = "unsupported_set_count"
        case repTotal = "rep_total"
        case volumeKgReps = "volume_kg_reps"
        case volumeExerciseCount = "volume_exercise_count"
        case exercises
    }
}

struct ExerciseOccurrenceAnalyticsDTO: Codable, Equatable, Sendable, Identifiable {
    let occurrenceIdentity: String
    let sourceExerciseId: String
    let displayName: String
    let exerciseOrder: Int
    let metricFamily: String
    let recordedSetCount: Int
    let workingSetCount: Int
    let warmupSetCount: Int
    let unsupportedSetCount: Int
    let repTotal: Int?
    let maxLoadKg: String?
    let topLoadSet: TopLoadSetEvidenceDTO?
    let volumeKgReps: String?
    let durationSeconds: String?
    let distanceMeters: String?
    let maxRpe: String?
    let metricCompleteness: String
    let reasonCodes: [String]

    var id: String { occurrenceIdentity }

    private enum CodingKeys: String, CodingKey {
        case occurrenceIdentity = "occurrence_identity"
        case sourceExerciseId = "source_exercise_id"
        case displayName = "display_name"
        case exerciseOrder = "exercise_order"
        case metricFamily = "metric_family"
        case recordedSetCount = "recorded_set_count"
        case workingSetCount = "working_set_count"
        case warmupSetCount = "warmup_set_count"
        case unsupportedSetCount = "unsupported_set_count"
        case repTotal = "rep_total"
        case maxLoadKg = "max_load_kg"
        case topLoadSet = "top_load_set"
        case volumeKgReps = "volume_kg_reps"
        case durationSeconds = "duration_seconds"
        case distanceMeters = "distance_meters"
        case maxRpe = "max_rpe"
        case metricCompleteness = "metric_completeness"
        case reasonCodes = "reason_codes"
    }
}

struct TopLoadSetEvidenceDTO: Codable, Equatable, Sendable {
    let setIdentity: String
    let setIndex: Int
    let setType: String
    let reps: Int
    let loadKg: String
    let rpe: String?

    private enum CodingKeys: String, CodingKey {
        case setIdentity = "set_identity"
        case setIndex = "set_index"
        case setType = "set_type"
        case reps
        case loadKg = "load_kg"
        case rpe
    }
}

// MARK: - GET /v1/training/exercises

struct ExerciseIndexResponse: Codable, Equatable, Sendable {
    let policyVersion: String
    let completeness: TrainingHistoryCompletenessDTO
    let exercises: [ExerciseIndexEntryDTO]

    private enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case completeness
        case exercises
    }
}

struct ExerciseIndexEntryDTO: Codable, Equatable, Sendable, Identifiable {
    let sourceSystem: String
    let sourceExerciseId: String
    let latestDisplayName: String
    let lastPerformedAt: Date
    let sessionCount: Int
    let latestMetricFamily: String
    let latestTopLoadSet: TopLoadSetEvidenceDTO?
    let frequency: ExerciseFrequencyDTO

    var id: String { "\(sourceSystem):\(sourceExerciseId)" }

    private enum CodingKeys: String, CodingKey {
        case sourceSystem = "source_system"
        case sourceExerciseId = "source_exercise_id"
        case latestDisplayName = "latest_display_name"
        case lastPerformedAt = "last_performed_at"
        case sessionCount = "session_count"
        case latestMetricFamily = "latest_metric_family"
        case latestTopLoadSet = "latest_top_load_set"
        case frequency
    }
}

struct ExerciseFrequencyDTO: Codable, Equatable, Sendable {
    let timezone: String
    let asOfDate: String
    let sessionsLast7Days: Int
    let sessionsLast28Days: Int
    let daysSinceLastPerformance: Int?

    private enum CodingKeys: String, CodingKey {
        case timezone
        case asOfDate = "as_of_date"
        case sessionsLast7Days = "sessions_last_7_days"
        case sessionsLast28Days = "sessions_last_28_days"
        case daysSinceLastPerformance = "days_since_last_performance"
    }
}

// MARK: - GET /v1/training/exercises/{source_exercise_id}/history

struct ExerciseTrainingHistoryResponse: Codable, Equatable, Sendable {
    let policyVersion: String
    let sourceSystem: String
    let sourceExerciseId: String
    let latestDisplayName: String
    let history: [ExerciseHistoryPointDTO]
    let latest: ExerciseHistoryPointDTO
    let previous: ExerciseHistoryPointDTO?
    let comparison: ExerciseSessionComparisonDTO
    let frequency: ExerciseFrequencyDTO
    let prEvidence: [TrainingPREvidenceDTO]
    let completeness: TrainingHistoryCompletenessDTO
    let coaching: ExerciseCoachingGuidanceDTO

    private enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case sourceSystem = "source_system"
        case sourceExerciseId = "source_exercise_id"
        case latestDisplayName = "latest_display_name"
        case history
        case latest
        case previous
        case comparison
        case frequency
        case prEvidence = "pr_evidence"
        case completeness
        case coaching
    }
}

struct ExerciseCoachingGuidanceDTO: Codable, Equatable, Sendable {
    let policyVersion: String
    let analyticsPolicyVersion: String
    let timezone: String
    let asOfDate: String
    let status: String
    let action: String?
    let metricFamily: String?
    let latestRevisionId: String?
    let previousRevisionId: String?
    let latestStartedAt: Date?
    let previousStartedAt: Date?
    let target: TrainingCoachingTargetDTO?
    let reasonCodes: [String]
    let limitations: [String]

    private enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case analyticsPolicyVersion = "analytics_policy_version"
        case timezone
        case asOfDate = "as_of_date"
        case status
        case action
        case metricFamily = "metric_family"
        case latestRevisionId = "latest_revision_id"
        case previousRevisionId = "previous_revision_id"
        case latestStartedAt = "latest_started_at"
        case previousStartedAt = "previous_started_at"
        case target
        case reasonCodes = "reason_codes"
        case limitations
    }
}

struct TrainingCoachingTargetDTO: Codable, Equatable, Sendable {
    let workingSetCount: Int
    let topLoadKg: String?
    let topSetReps: Int?
    let totalReps: Int?
    let keepAssistanceConstant: Bool

    private enum CodingKeys: String, CodingKey {
        case workingSetCount = "working_set_count"
        case topLoadKg = "top_load_kg"
        case topSetReps = "top_set_reps"
        case totalReps = "total_reps"
        case keepAssistanceConstant = "keep_assistance_constant"
    }
}

struct ExerciseHistoryPointDTO: Codable, Equatable, Sendable, Identifiable {
    let revisionId: String
    let sourceSessionId: String
    let sourceRevision: String
    let sessionTitle: String
    let startedAt: Date
    let displayName: String
    let occurrenceCount: Int
    let metricFamily: String
    let recordedSetCount: Int
    let workingSetCount: Int
    let warmupSetCount: Int
    let unsupportedSetCount: Int
    let repTotal: Int?
    let maxLoadKg: String?
    let topLoadSet: TopLoadSetEvidenceDTO?
    let volumeKgReps: String?
    let maxRpe: String?
    let metricCompleteness: String

    var id: String { revisionId }

    private enum CodingKeys: String, CodingKey {
        case revisionId = "revision_id"
        case sourceSessionId = "source_session_id"
        case sourceRevision = "source_revision"
        case sessionTitle = "session_title"
        case startedAt = "started_at"
        case displayName = "display_name"
        case occurrenceCount = "occurrence_count"
        case metricFamily = "metric_family"
        case recordedSetCount = "recorded_set_count"
        case workingSetCount = "working_set_count"
        case warmupSetCount = "warmup_set_count"
        case unsupportedSetCount = "unsupported_set_count"
        case repTotal = "rep_total"
        case maxLoadKg = "max_load_kg"
        case topLoadSet = "top_load_set"
        case volumeKgReps = "volume_kg_reps"
        case maxRpe = "max_rpe"
        case metricCompleteness = "metric_completeness"
    }
}

struct ExerciseSessionComparisonDTO: Codable, Equatable, Sendable {
    let latestRevisionId: String
    let previousRevisionId: String?
    let workingSetCountDelta: Int?
    let topLoadDeltaKg: String?
    let repsAtSameTopLoadDelta: Int?
    let volumeDeltaKgReps: String?
    let reasonCodes: [String]

    private enum CodingKeys: String, CodingKey {
        case latestRevisionId = "latest_revision_id"
        case previousRevisionId = "previous_revision_id"
        case workingSetCountDelta = "working_set_count_delta"
        case topLoadDeltaKg = "top_load_delta_kg"
        case repsAtSameTopLoadDelta = "reps_at_same_top_load_delta"
        case volumeDeltaKgReps = "volume_delta_kg_reps"
        case reasonCodes = "reason_codes"
    }
}

struct TrainingPREvidenceDTO: Codable, Equatable, Sendable, Identifiable {
    let evidenceId: String
    let prType: String
    let revisionId: String
    let sourceSessionId: String
    let observedAt: Date
    let loadKg: String?
    let reps: Int?
    let volumeKgReps: String?
    let scope: String

    var id: String { evidenceId }

    private enum CodingKeys: String, CodingKey {
        case evidenceId = "evidence_id"
        case prType = "pr_type"
        case revisionId = "revision_id"
        case sourceSessionId = "source_session_id"
        case observedAt = "observed_at"
        case loadKg = "load_kg"
        case reps
        case volumeKgReps = "volume_kg_reps"
        case scope
    }
}

struct TrainingHistoryCompletenessDTO: Codable, Equatable, Sendable {
    let sourceBootstrapComplete: Bool
    let queryComplete: Bool
    let lifetimeGuaranteed: Bool
    let wording: String

    private enum CodingKeys: String, CodingKey {
        case sourceBootstrapComplete = "source_bootstrap_complete"
        case queryComplete = "query_complete"
        case lifetimeGuaranteed = "lifetime_guaranteed"
        case wording
    }
}

// MARK: - Existing GET /v1/training/sessions/{revision_id}

struct DetailedTrainingSessionResponse: Codable, Equatable, Sendable {
    let revisionId: String
    let sourceSystem: String
    let sourceSessionId: String
    let sourceRevision: String
    let title: String
    let description: String?
    let routineId: String?
    let startedAt: Date
    let endedAt: Date
    let sourceCreatedAt: Date?
    let sourceUpdatedAt: Date?
    let parserVersion: String
    let sourcePayloadSha256: String
    let ingestedAt: Date
    let exercises: [DetailedTrainingExerciseDTO]

    private enum CodingKeys: String, CodingKey {
        case revisionId = "revision_id"
        case sourceSystem = "source_system"
        case sourceSessionId = "source_session_id"
        case sourceRevision = "source_revision"
        case title
        case description
        case routineId = "routine_id"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case sourceCreatedAt = "source_created_at"
        case sourceUpdatedAt = "source_updated_at"
        case parserVersion = "parser_version"
        case sourcePayloadSha256 = "source_payload_sha256"
        case ingestedAt = "ingested_at"
        case exercises
    }
}

struct DetailedTrainingExerciseDTO: Codable, Equatable, Sendable, Identifiable {
    let occurrenceIdentity: String
    let sourceExerciseId: String
    let displayName: String
    let exerciseOrder: Int
    let notes: String?
    let supersetId: Int?
    let sets: [DetailedTrainingSetDTO]

    var id: String { occurrenceIdentity }

    private enum CodingKeys: String, CodingKey {
        case occurrenceIdentity = "occurrence_identity"
        case sourceExerciseId = "source_exercise_id"
        case displayName = "display_name"
        case exerciseOrder = "exercise_order"
        case notes
        case supersetId = "superset_id"
        case sets
    }
}

struct DetailedTrainingSetDTO: Codable, Equatable, Sendable, Identifiable {
    let setIdentity: String
    let sourceSetId: String?
    let setIndex: Int
    let setType: String
    let reps: Int?
    let load: DetailedTrainingMeasurementDTO?
    let distance: DetailedTrainingMeasurementDTO?
    let durationSeconds: String?
    let rpe: String?
    let customMetric: String?

    var id: String { setIdentity }

    private enum CodingKeys: String, CodingKey {
        case setIdentity = "set_identity"
        case sourceSetId = "source_set_id"
        case setIndex = "set_index"
        case setType = "set_type"
        case reps
        case load
        case distance
        case durationSeconds = "duration_seconds"
        case rpe
        case customMetric = "custom_metric"
    }
}

struct DetailedTrainingMeasurementDTO: Codable, Equatable, Sendable {
    let value: String
    let unit: String
}
