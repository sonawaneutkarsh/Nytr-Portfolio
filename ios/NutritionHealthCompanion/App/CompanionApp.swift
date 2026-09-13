import SwiftUI

@main
struct CompanionApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage(NytrAppearance.preferenceKey) private var appearance = NytrAppearance.system.rawValue

    @State private var viewModel: HealthSyncViewModel
    @State private var sessionViewModel: SessionViewModel
    @State private var signInViewModel: SignInViewModel
    @State private var todayViewModel: TodayViewModel
    @State private var nutritionHistoryViewModel: NutritionHistoryViewModel
    @State private var progressViewModel: ProgressViewModel
    @State private var targetReviewViewModel: TargetReviewViewModel
    @State private var bodyGoalsViewModel: BodyGoalsViewModel
    @State private var manualFoodViewModel: ManualFoodViewModel
    @State private var trainingViewModel: TrainingViewModel
    @State private var aiReviewViewModel: AIReviewViewModel
    @State private var notificationViewModel: MealGuidanceNotificationViewModel
    @State private var initialSessionCheckCompleted = false

    init() {
        let config = Bundle.main
        func cfg(_ key: String) -> String {
            (config.object(forInfoDictionaryKey: key) as? String) ?? ""
        }
        // Non-secret configuration from Config.xcconfig via Info.plist.
        let backendBase =
            URL(string: cfg("BACKEND_BASE_URL"))
            ?? URL(string: "https://localhost")!
        let supabaseURL =
            URL(string: cfg("SUPABASE_URL"))
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
        let notificationViewModel = MealGuidanceNotificationViewModel()
        _notificationViewModel = State(initialValue: notificationViewModel)
        let aiReviewViewModel = AIReviewViewModel(
            backend: client,
            onUnauthorized: { sessionViewModel.signOut() }
        )
        _aiReviewViewModel = State(initialValue: aiReviewViewModel)
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
            },
            onNutritionRecorded: {
                aiReviewViewModel.invalidateEvidenceAfterConsumption()
            },
            onPlanStateChanged: { plan, consumption, timezone in
                await notificationViewModel.reconcile(
                    plan: plan, consumption: consumption, timezone: timezone
                )
            }
        )
        _todayViewModel = State(initialValue: todayViewModel)
        _manualFoodViewModel = State(
            initialValue: ManualFoodViewModel(
                backend: client,
                onRecorded: {
                    aiReviewViewModel.invalidateEvidenceAfterConsumption()
                    async let today: Void = todayViewModel.refreshNutrition()
                    async let history: Void = nutritionHistoryViewModel.refresh()
                    _ = await (today, history)
                }
            ))
        let targetReviewViewModel = TargetReviewViewModel(
            backend: client,
            onUnauthorized: { sessionViewModel.signOut() },
            onProteinTargetApproved: {
                await todayViewModel.refresh()
                await progressViewModel.refresh()
            }
        )
        _targetReviewViewModel = State(initialValue: targetReviewViewModel)
        let bodyGoalsViewModel = BodyGoalsViewModel(
            backend: client,
            onUnauthorized: { sessionViewModel.signOut() },
            onTargetsChanged: {
                await todayViewModel.refresh()
                await progressViewModel.refresh()
                await targetReviewViewModel.retryGoalLoad()
                await targetReviewViewModel.retryProteinLoad()
            }
        )
        _bodyGoalsViewModel = State(initialValue: bodyGoalsViewModel)
        _trainingViewModel = State(
            initialValue: TrainingViewModel(
                backend: client,
                onUnauthorized: { sessionViewModel.signOut() }
            ))
        _viewModel = State(
            initialValue: HealthSyncViewModel(
                engine: engine,
                workoutEngine: workoutEngine,
                backend: client,
                store: store,
                workoutStore: workoutStore,
                onSyncCompleted: {
                    Task {
                        await todayViewModel.refresh()
                        await progressViewModel.refresh()
                        await bodyGoalsViewModel.refresh()
                    }
                }
            ))

        #if canImport(UserNotifications)
            NytrNotificationDelegate.shared.install()
        #endif

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
                bodyGoalsViewModel: bodyGoalsViewModel,
                manualFoodViewModel: manualFoodViewModel,
                trainingViewModel: trainingViewModel,
                aiReviewViewModel: aiReviewViewModel,
                notificationViewModel: notificationViewModel,
                appearance: NytrAppearance.binding($appearance)
            )
            .preferredColorScheme(
                (NytrAppearance(rawValue: appearance) ?? .system).colorScheme
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
            .onOpenURL { url in
                if MealGuidanceDeepLink.destination(from: url) == nil {
                    sessionViewModel.handleOpenURL(url)
                }
            }
            .onChange(of: sessionViewModel.phase) { _, phase in
                if phase == .signedOut {
                    notificationViewModel.resetForSignOut()
                }
            }
        }
        .onChange(of: scenePhase) { _, newPhase in
            if newPhase == .active,
                initialSessionCheckCompleted,
                sessionViewModel.shouldStartForegroundSync()
            {
                // Reliable foreground catch-up when stale (plan §4).
                Task {
                    await notificationViewModel.refreshPermission()
                    await viewModel.syncNow()
                }
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
    let bodyGoalsViewModel: BodyGoalsViewModel
    let manualFoodViewModel: ManualFoodViewModel
    let trainingViewModel: TrainingViewModel
    let aiReviewViewModel: AIReviewViewModel
    let notificationViewModel: MealGuidanceNotificationViewModel
    @Binding var appearance: NytrAppearance
    @State private var selectedTab = RootTab.today
    @State private var focusedMeal: MealGuidanceKind?
    @State private var showingSettings = false

    private enum RootTab: Hashable { case today, food, training, progress }

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
            TabView(selection: $selectedTab) {
                TodayView(
                    viewModel: todayViewModel,
                    nutritionHistoryViewModel: nutritionHistoryViewModel,
                    progressViewModel: progressViewModel,
                    targetReviewViewModel: targetReviewViewModel,
                    bodyGoalsViewModel: bodyGoalsViewModel,
                    manualFoodViewModel: manualFoodViewModel,
                    aiReviewViewModel: aiReviewViewModel,
                    notificationViewModel: notificationViewModel,
                    healthSyncViewModel: viewModel,
                    subject: subject,
                    onSignOut: {
                        sessionViewModel.signOut {
                            todayViewModel.clearForSignOut()
                            nutritionHistoryViewModel.resetForSignOut()
                            progressViewModel.resetForSignOut()
                            targetReviewViewModel.resetForSignOut()
                            bodyGoalsViewModel.resetForSignOut()
                            trainingViewModel.resetForSignOut()
                            manualFoodViewModel.resetForSignOut()
                            aiReviewViewModel.resetForSignOut()
                            notificationViewModel.resetForSignOut()
                        }
                    },
                    onShowSettings: { showingSettings = true }
                )
                .tabItem {
                    Label("Today", systemImage: "sun.max")
                }
                .tag(RootTab.today)

                NavigationStack {
                    ManualFoodView(
                        viewModel: manualFoodViewModel,
                        todayViewModel: todayViewModel,
                        nutritionHistoryViewModel: nutritionHistoryViewModel,
                        subject: subject,
                        focusedMeal: focusedMeal,
                        onShowSettings: { showingSettings = true }
                    )
                }
                    .tabItem { Label("Food", systemImage: "fork.knife") }
                    .tag(RootTab.food)

                TrainingView(
                    viewModel: trainingViewModel, subject: subject,
                    onShowSettings: { showingSettings = true }
                )
                    .tabItem {
                        Label("Training", systemImage: "figure.strengthtraining.traditional")
                    }
                    .tag(RootTab.training)
                NavigationStack {
                    OwnerProgressView(
                        viewModel: progressViewModel, bodyGoalsViewModel: bodyGoalsViewModel,
                        targetReviewViewModel: targetReviewViewModel, subject: subject,
                        onShowSettings: { showingSettings = true })
                }.tabItem { Label("Progress", systemImage: "chart.xyaxis.line") }
                    .tag(RootTab.progress)
            }
            .tint(NytrDesign.accent)
            .sheet(isPresented: $showingSettings) {
                NavigationStack {
                    SettingsView(
                        appearance: $appearance,
                        healthSyncViewModel: viewModel,
                        notificationViewModel: notificationViewModel,
                        onSignOut: onSignOutFromSettings
                    )
                    .toolbar {
                        ToolbarItem(placement: .confirmationAction) {
                            Button("Done") { showingSettings = false }
                        }
                    }
                }
                // A sheet is hosted in its own presentation context, so the app
                // root's preferredColorScheme only styles the window behind it
                // and an already-presented Settings hierarchy keeps the scheme it
                // was presented with. Deriving the same scheme from the same
                // binding here recolors the open sheet, its Form surfaces, its
                // navigation bar, and anything it pushes on the first tap.
                // This reads the single persisted preference; it does not own it.
                .preferredColorScheme(appearance.colorScheme)
            }
            .onOpenURL(perform: openMealLink)
            .onReceive(NotificationCenter.default.publisher(for: .nytrMealNotificationOpened)) {
                notification in
                #if canImport(UserNotifications)
                    let url = NytrNotificationDelegate.shared.takePendingDeepLink()
                        ?? notification.object as? URL
                #else
                    let url = notification.object as? URL
                #endif
                guard let url else { return }
                openMealLink(url)
            }
            .task(id: subject) {
                #if canImport(UserNotifications)
                    if let url = NytrNotificationDelegate.shared.takePendingDeepLink() {
                        openMealLink(url)
                    }
                #endif
            }
        }
    }

    private func openMealLink(_ url: URL) {
        guard let meal = MealGuidanceDeepLink.destination(from: url) else { return }
        focusedMeal = meal
        selectedTab = .food
    }

    private func onSignOutFromSettings() {
        showingSettings = false
        sessionViewModel.signOut {
            todayViewModel.clearForSignOut()
            nutritionHistoryViewModel.resetForSignOut()
            progressViewModel.resetForSignOut()
            targetReviewViewModel.resetForSignOut()
            bodyGoalsViewModel.resetForSignOut()
            trainingViewModel.resetForSignOut()
            manualFoodViewModel.resetForSignOut()
            aiReviewViewModel.resetForSignOut()
            notificationViewModel.resetForSignOut()
        }
    }
}
