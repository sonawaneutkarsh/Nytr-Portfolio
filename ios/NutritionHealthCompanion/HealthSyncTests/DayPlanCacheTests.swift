import XCTest
@testable import NutritionHealthCompanion

final class DayPlanCacheTests: XCTestCase {
    private let subjectA = "00000000-0000-0000-0000-0000000000a1"
    private let subjectB = "00000000-0000-0000-0000-0000000000b2"
    private let cachedAt = WireDate.date(fromISO8601: "2026-08-21T12:30:00Z")!
    private var suiteName = ""
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "DayPlanCacheTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)!
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        super.tearDown()
    }

    func test_saveCompletedThenLoadReturnsExactResponseAndMetadata() {
        let response = completed(planDate: "2026-08-21", sha: "sha-one")
        let cache = makeCache()

        XCTAssertTrue(cache.save(response, forSubject: subjectA))
        let loaded = cache.load(forSubject: subjectA)

        XCTAssertEqual(loaded?.response, response)
        XCTAssertEqual(loaded?.cachedAt, cachedAt)
        XCTAssertEqual(loaded?.planDate, "2026-08-21")
    }

    func test_differentSubjectsDoNotCollide() {
        let cache = makeCache()
        let first = completed(planDate: "2026-08-21", sha: "sha-a")
        let second = completed(planDate: "2026-08-22", sha: "sha-b")

        XCTAssertTrue(cache.save(first, forSubject: subjectA))
        XCTAssertTrue(cache.save(second, forSubject: subjectB))

        XCTAssertEqual(cache.load(forSubject: subjectA)?.response, first)
        XCTAssertEqual(cache.load(forSubject: subjectB)?.response, second)
    }

    func test_subjectDigestIsStableAcrossInstancesAndMatchesSHA256Tripwire() {
        let expected = "dayplan.cache.v1."
            + "868425aba2aed381a87aa2863a1a873fa639d90c8e4bab2af2fce29a1f92aad3"
        XCTAssertEqual(DayPlanCache.cacheKey(forSubject: subjectA), expected)
        XCTAssertEqual(
            DayPlanCache.cacheKey(forSubject: subjectA),
            DayPlanCache.cacheKey(forSubject: subjectA))
        XCTAssertFalse(DayPlanCache.cacheKey(forSubject: subjectA).contains(subjectA))
    }

    func test_replacingSameSubjectReturnsOnlyNewestRecord() {
        let cache = makeCache()
        XCTAssertTrue(cache.save(
            completed(planDate: "2026-08-21", sha: "old"), forSubject: subjectA))
        XCTAssertTrue(cache.save(
            completed(planDate: "2026-08-22", sha: "new"), forSubject: subjectA))

        let loaded = cache.load(forSubject: subjectA)
        XCTAssertEqual(loaded?.planDate, "2026-08-22")
        guard case .completed(let completed) = loaded?.response else {
            return XCTFail("expected completed")
        }
        XCTAssertEqual(completed.planSha256, "new")
    }

    func test_corruptBytesAreClearedAndReturnMiss() {
        let key = DayPlanCache.cacheKey(forSubject: subjectA)
        defaults.set(Data([0x00, 0xff, 0x01]), forKey: key)

        XCTAssertNil(makeCache().load(forSubject: subjectA))
        XCTAssertNil(defaults.object(forKey: key))
    }

    func test_unknownSchemaVersionIsClearedAndReturnsMiss() throws {
        let response = completed(planDate: "2026-08-21", sha: "sha")
        let record = CachedDayPlan(
            schemaVersion: 999, planDate: "2026-08-21",
            cachedAt: cachedAt, response: response)
        let key = DayPlanCache.cacheKey(forSubject: subjectA)
        defaults.set(try JSONEncoder().encode(record), forKey: key)

        XCTAssertNil(makeCache().load(forSubject: subjectA))
        XCTAssertNil(defaults.object(forKey: key))
    }

    func test_malformedDayPlanResponseIsClearedAndReturnsMiss() {
        let key = DayPlanCache.cacheKey(forSubject: subjectA)
        defaults.set(Data("""
        {"schemaVersion":1,"planDate":"2026-08-21","cachedAt":0,
         "response":{"state":"future"}}
        """.utf8), forKey: key)

        XCTAssertNil(makeCache().load(forSubject: subjectA))
        XCTAssertNil(defaults.object(forKey: key))
    }

    func test_noPlanAndNotGeneratedAreRejectedWithoutReplacingCompleted() {
        let cache = makeCache()
        let original = completed(planDate: "2026-08-21", sha: "kept")
        XCTAssertTrue(cache.save(original, forSubject: subjectA))

        let noPlan = DayPlanResponse.noPlan(NoPlanDay(
            requestedDate: "2026-08-21", planDate: "2026-08-21",
            reasonCodes: ["empty_menu_period"], inputsFingerprint: "fp", runId: nil))
        let notGenerated = DayPlanResponse.notGenerated(
            NotGeneratedDay(requestedDate: "2026-08-21"))

        XCTAssertFalse(cache.save(noPlan, forSubject: subjectA))
        XCTAssertFalse(cache.save(notGenerated, forSubject: subjectA))
        XCTAssertEqual(cache.load(forSubject: subjectA)?.response, original)
    }

    func test_clearSubjectRemovesOnlyThatSubject() {
        let cache = makeCache()
        XCTAssertTrue(cache.save(
            completed(planDate: "2026-08-21", sha: "a"), forSubject: subjectA))
        XCTAssertTrue(cache.save(
            completed(planDate: "2026-08-22", sha: "b"), forSubject: subjectB))

        cache.clear(forSubject: subjectA)

        XCTAssertNil(cache.load(forSubject: subjectA))
        XCTAssertNotNil(cache.load(forSubject: subjectB))
    }

    func test_clearAllRemovesOnlyDayPlanKeys() {
        let cache = makeCache()
        defaults.set("keep", forKey: "unrelated.preference")
        defaults.set(Data("old".utf8), forKey: "dayplan.cache.v0.legacy")
        XCTAssertTrue(cache.save(
            completed(planDate: "2026-08-21", sha: "a"), forSubject: subjectA))
        XCTAssertTrue(cache.save(
            completed(planDate: "2026-08-22", sha: "b"), forSubject: subjectB))

        cache.clearAll()

        XCTAssertNil(cache.load(forSubject: subjectA))
        XCTAssertNil(cache.load(forSubject: subjectB))
        XCTAssertNil(defaults.object(forKey: "dayplan.cache.v0.legacy"))
        XCTAssertEqual(defaults.string(forKey: "unrelated.preference"), "keep")
    }

    func test_cacheContainsNoSubjectOrAuthenticationMaterial() {
        let cache = makeCache()
        XCTAssertTrue(cache.save(
            completed(planDate: "2026-08-21", sha: "sha"), forSubject: subjectA))

        let key = DayPlanCache.cacheKey(forSubject: subjectA)
        let data = defaults.data(forKey: key)!
        let stored = String(data: data, encoding: .utf8)!
        XCTAssertFalse(key.contains(subjectA))
        XCTAssertFalse(stored.contains(subjectA))
        XCTAssertFalse(stored.contains("access_token"))
        XCTAssertFalse(stored.contains("refresh_token"))
        XCTAssertFalse(stored.contains("Bearer "))
    }

    private func makeCache() -> DayPlanCache {
        DayPlanCache(defaults: defaults, now: { self.cachedAt })
    }

    private func completed(planDate: String, sha: String) -> DayPlanResponse {
        let artifact = DailyPlanArtifact(
            artifactKind: "daily_plan",
            planDate: planDate,
            policyVersions: PlanPolicyVersions(
                engine: "engine.v1", planner: "planner.v1",
                schedule: "schedule.v1", target: "target.v1"),
            menuSnapshotSha256: "menu-sha",
            slots: [],
            status: "ok"
        )
        return .completed(CompletedDayPlan(
            requestedDate: planDate,
            planDate: planDate,
            planSha256: sha,
            inputsFingerprint: "fingerprint-\(sha)",
            runId: UUID(uuidString: "10000000-0000-0000-0000-000000000001")!,
            versionId: UUID(uuidString: "20000000-0000-0000-0000-000000000001")!,
            plan: artifact,
            planItems: [],
            targetPolicy: nil,
            generatedAt: WireDate.date(fromISO8601: "2026-08-21T12:00:00Z")
        ))
    }
}
