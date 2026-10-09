//
//  SceneDelegate.swift
//  e-help
//
//  Created by Jorgon on 2026/9/21.
//

import UIKit

class SceneDelegate: UIResponder, UIWindowSceneDelegate {

    var window: UIWindow?


    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        // Use this method to optionally configure and attach the UIWindow `window` to the provided UIWindowScene `scene`.
        // If using a storyboard, the `window` property will automatically be initialized and attached to the scene.
        // This delegate does not imply the connecting scene or session are new (see `application:configurationForConnectingSceneSession` instead).
        
         // 1. scene 必须是 UIWindowScene 类型
//             guard let windowScene = (scene as? UIWindowScene) else { return }
//
//             // 2. 用这个 windowScene 创建 window（而不是 UIWindow(frame:)）
//             let window = UIWindow(windowScene: windowScene)
//
//             // 3. 创建你想要的根控制器
//             let mainVC = MainViewController()          // 你自己的控制器
//             // 如果需要导航栈，就包一层：
//             // let nav = UINavigationController(rootViewController: mainVC)
//
//             window.rootViewController = mainVC          // 或 nav
//
//             // 4. 挂到属性上并显示
//             self.window = window
//             window.makeKeyAndVisible()
         
        
        print(#function)
        guard let _ = (scene as? UIWindowScene) else { return }
    }

    func sceneDidDisconnect(_ scene: UIScene) {
        print(#function)
        // Called as the scene is being released by the system.
        // This occurs shortly after the scene enters the background, or when its session is discarded.
        // Release any resources associated with this scene that can be re-created the next time the scene connects.
        // The scene may re-connect later, as its session was not necessarily discarded (see `application:didDiscardSceneSessions` instead).
    }

    func sceneDidBecomeActive(_ scene: UIScene) {
        print(#function)
        // Called when the scene has moved from an inactive state to an active state.
        // Use this method to restart any tasks that were paused (or not yet started) when the scene was inactive.
    }

    func sceneWillResignActive(_ scene: UIScene) {
        print(#function)
        // Called when the scene will move from an active state to an inactive state.
        // This may occur due to temporary interruptions (ex. an incoming phone call).
    }

    func sceneWillEnterForeground(_ scene: UIScene) {
        // Called as the scene transitions from the background to the foreground.
        // Use this method to undo the changes made on entering the background.
    }

    func sceneDidEnterBackground(_ scene: UIScene) {
        // Called as the scene transitions from the foreground to the background.
        // Use this method to save data, release shared resources, and store enough scene-specific state information
        // to restore the scene back to its current state.
    }
    
    func windowScene(_ windowScene: UIWindowScene, didUpdateEffectiveGeometry previousEffectiveGeometry: UIWindowScene.Geometry) {
        let geometry = windowScene.effectiveGeometry
        let availableSpace = geometry.coordinateSpace.bounds
    }


}

