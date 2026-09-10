import SwiftUI

@main
struct CompanionApp: App {
    @Environment(\.scenePhase) private var scenePhase

    @State private var viewModel: HealthSyncViewModel
    @State private var sessionViewModel: SessionViewModel
    @State private var signInViewModel: SignInViewModel
    @State private var todayViewModel: TodayViewModel
    @State private var nutritionHistoryViewModel: NutritionHistoryViewModel
    @State private var progressViewModel: ProgressViewModel
    @State private var targetReviewViewModel: TargetReviewViewModel
    @State private var manualFoodViewModel: ManualFoodViewModel
    @State private var trainingViewModel: TrainingViewModel
    @State private var aiReviewViewModel: AIReviewViewModel
    @State private var bodyGoalsViewModel: BodyGoalsViewModel
    @State private var initialSessionCheckCompleted = false

    init() {
        let config = Bundle.main
        func cfg(_ key: String) -> String {
            (config.object(forInfoDictionaryKey: key) as? String) ?? ""
        }
        // Non-secret configuration from Config.xcconfig via Info.plist.
        let backendBase = URL(string: cfg("BACKEND_BASE_URL"))
            ?? URL(string: "https://localhost")!
        let supabaseURL = URL(string: cfg("SUPABASE_URL"))
            ?? URL(string: "https://localhost")!
        let anonKey = cfg("SUPABASE_ANON_KEY")

        let adapter = HealthKitBodyMassAdapter()
        let authSession = SupabaseAuthSession(
            supabaseURL: supabaseURL, anonKey: anonKey,
            tokens: KeychainTokenStore()
        )
        let client = HTTPBackendClient(baseURL: backendBase, auth: authSession)
        let store = UserDefaultsDurableStore()
        let workoutStore = UserDefaultsDurableStore(namespace: .workout)

        // THE single shared sync execution path, owned by the app dependency
        // graph. syncNow(), scenePhase activation, and observer/background
        // wakes all converge on this instance; no other component constructs
        // a HealthSyncCoordinator.
        let engine = HealthSyncEngine(
            reading: adapter, backend: client, store: store, auth: authSession
        )
        let workoutEngine = WorkoutSyncEngine(
            reading: adapter, backend: client, store: workoutStore, auth: authSession
        )
        let sessionViewModel = SessionViewModel(auth: authSession)
        _sessionViewModel = State(initialValue: sessionViewModel)
        _signInViewModel = State(initialValue: SignInViewModel(requester: authSession))
        let nutritionHistoryViewModel = NutritionHistoryViewModel(
            backend: client,
            onUnauthorized: { sessionViewModel.signOut() }
        )
        _nutritionHistoryViewModel = State(initialValue: nutritionHistoryViewModel)
        let progressViewModel = ProgressViewModel(
            backend: client,
            onUnauthorized: { sessionViewModel.signOut() }
        )
        _progressViewModel = State(initialValue: progressViewModel)
        let todayViewModel = TodayViewModel(
            backend: client,
            cache: DayPlanCache(),
            onUnauthorized: { sessionViewModel.signOut() },
            onNextMealConsumptionRecorded: {
                await nutritionHistoryViewModel.refresh()
                await progressViewModel.refresh()
            }
        )
        _todayViewModel = State(initialValue: todayViewModel)
        _manualFoodViewModel = State(initialValue: ManualFoodViewModel(
            backend: client,
            onRecorded: {
                await todayViewModel.refresh()
                await nutritionHistoryViewModel.refresh()
                await progressViewModel.refresh()
            }
        ))
        _targetReviewViewModel = State(initialValue: TargetReviewViewModel(
            backend: client,
            onUnauthorized: { sessionViewModel.signOut() },
            onProteinTargetApproved: {
                await todayViewModel.refresh()
                await progressViewModel.refresh()
            }
        ))
        _trainingViewModel = State(initialValue: TrainingViewModel(
            backend: client,
            onUnauthorized: { sessionViewModel.signOut() }
        ))
        _aiReviewViewModel = State(initialValue: AIReviewViewModel(
            backend: client,
            onUnauthorized: { sessionViewModel.signOut() }
        ))
        _bodyGoalsViewModel = State(initialValue: BodyGoalsViewModel(
            backend: client,
            onUnauthorized: { sessionViewModel.signOut() }
        ))
        _viewModel = State(initialValue: HealthSyncViewModel(
            engine: engine,
            workoutEngine: workoutEngine,
            backend: client,
            store: store,
            workoutStore: workoutStore,
            onSyncCompleted: {
                Task {
                    await todayViewModel.refresh()
                    await progressViewModel.refresh()
                }
            }
        ))

        // Best-effort observer + background delivery registration at launch
        // (plan §9). Correctness never depends on these firing.
        if adapter.isAvailable() {
            adapter.changesInBackground = { [weak engine] in
                guard let engine else { return }
                _ = await engine.runIncrementalSync()
            }
            Task { @MainActor in
                await adapter.enableBackgroundDeliveryIfPossible()
                adapter.registerObserver()
            }
        }
    }

    var body: some Scene {
        WindowGroup {
            RootView(
                viewModel: viewModel,
                sessionViewModel: sessionViewModel,
                signInViewModel: signInViewModel,
                todayViewModel: todayViewModel,
                nutritionHistoryViewModel: nutritionHistoryViewModel,
                progressViewModel: progressViewModel,
                targetReviewViewModel: targetReviewViewModel,
                manualFoodViewModel: manualFoodViewModel,
                trainingViewModel: trainingViewModel,
                aiReviewViewModel: aiReviewViewModel,
                bodyGoalsViewModel: bodyGoalsViewModel
            )
            .task {
                guard !initialSessionCheckCompleted else { return }
                await sessionViewModel.checkSession()
                initialSessionCheckCompleted = true
                // A cold launch commonly becomes active while session
                // restoration is still checking. Catch up once the restored
                // session is known instead of waiting for another scene event.
                if scenePhase == .active, sessionViewModel.shouldStartForegroundSync() {
                    await viewModel.syncNow()
                }
            }
            .onOpenURL { url in sessionViewModel.handleOpenURL(url) }
        }
        .onChange(of: scenePhase) { _, newPhase in
            if newPhase == .active,
               initialSessionCheckCompleted,
               sessionViewModel.shouldStartForegroundSync()
            {
                // Reliable foreground catch-up when stale (plan §4).
                Task { await viewModel.syncNow() }
            } else if newPhase != .active {
                sessionViewModel.sceneDidLeaveActiveState()
            }
        }
    }
}

struct RootView: View {
    let viewModel: HealthSyncViewModel
    let sessionViewModel: SessionViewModel
    let signInViewModel: SignInViewModel
    let todayViewModel: TodayViewModel
    let nutritionHistoryViewModel: NutritionHistoryViewModel
    let progressViewModel: ProgressViewModel
    let targetReviewViewModel: TargetReviewViewModel
    let manualFoodViewModel: ManualFoodViewModel
    let trainingViewModel: TrainingViewModel
    let aiReviewViewModel: AIReviewViewModel
    let bodyGoalsViewModel: BodyGoalsViewModel

    var body: some View {
        switch sessionViewModel.phase {
        case .checking:
            ProgressView("Checking session…")
        case .signedOut:
            SignInView(
                viewModel: signInViewModel,
                callbackError: sessionViewModel.callbackError
            )
        case .signedIn(let subject):
            TabView {
                TodayView(
                    viewModel: todayViewModel,
                    nutritionHistoryViewModel: nutritionHistoryViewModel,
                    progressViewModel: progressViewModel,
                    targetReviewViewModel: targetReviewViewModel,
                    manualFoodViewModel: manualFoodViewModel,
                    aiReviewViewModel: aiReviewViewModel,
                    bodyGoalsViewModel: bodyGoalsViewModel,
                    healthSyncViewModel: viewModel,
                    subject: subject,
                    onSignOut: {
                        sessionViewModel.signOut {
                            todayViewModel.clearForSignOut()
                            nutritionHistoryViewModel.resetForSignOut()
                            progressViewModel.resetForSignOut()
                            targetReviewViewModel.resetForSignOut()
                            trainingViewModel.resetForSignOut()
                            manualFoodViewModel.resetForSignOut()
                            aiReviewViewModel.resetForSignOut()
                            bodyGoalsViewModel.resetForSignOut()
                        }
                    }
                )
                .tabItem {
                    Label("Today", systemImage: "sun.max")
                }

                TrainingView(viewModel: trainingViewModel, subject: subject)
                    .tabItem {
                        Label("Training", systemImage: "figure.strengthtraining.traditional")
                    }
            }
        }
    }
}
