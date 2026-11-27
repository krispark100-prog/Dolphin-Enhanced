#!/usr/bin/env python3
"""Aplica correcciones minimas sobre los archivos ACTUALES del fork (no los reemplaza).

Cada parche busca un fragmento (ignorando diferencias de espacios) y lo sustituye.
Si el fragmento no existe en tu rama, ese parche se omite con un aviso y el build sigue.
Es idempotente: si ya se aplico, no hace nada.
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
applied, skipped, missing, diag = [], [], [], []

import subprocess

# --------------------------------------------------------------------------- FASE 0
# Si el fork aun contiene archivos COMPLETOS del paquete anterior (v1), se restauran desde el
# historial de git a la version que tenia tu rama justo antes de subirlos. Asi no hay que
# revertir nada a mano. Cada marcador es un texto que solo existe en los archivos v1.
V1_MARKERS = {
    "Android/app/build.gradle": "which breaks the IEEE-754 behaviour",
    "ui/main/MainActivity.kt": "We only ever read games",
    "model/GameFileCache.kt": "Scan only folders that still exist",
    "services/GameFileCacheService.java": "final GameFileCache cache = gameFileCache;",
    "fragments/EmulationFragment.kt": "Capture the Surface now: the field can be nulled",
    "jni/GameList/GameFileCache.cpp": "big libraries overflow the JNI local reference table and the app aborts",
    "jni/MainAndroid.cpp": "Failed to boot the selected game.",
    "Core/Core/Core.cpp": "IsLittleCorePart",
    "Core/Core/Config/GraphicsSettings.cpp":
        '"ShaderCompilationMode"},\n    ShaderCompilationMode::AsynchronousSkipRendering}',
}
restored, restore_errors = [], []


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, text=True)


def restore_v1_files():
    top = git("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return
    top = Path(top.stdout.strip())
    for suffix, marker in V1_MARKERS.items():
        path = None
        for p in ROOT.rglob("*"):
            if p.is_file() and p.as_posix().endswith(suffix) and "/build/" not in p.as_posix() \
                    and "/.cxx/" not in p.as_posix():
                path = p
                break
        if path is None:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if marker not in text or "[fix" in text:
            continue
        rel = path.relative_to(top).as_posix()
        # ultimo commit que cambio la cantidad de apariciones del marcador (= el que lo agrego)
        log = git("log", "-1", "--format=%H", f"-S{marker}", "--", rel, cwd=top)
        sha = log.stdout.strip()
        if not sha:
            restore_errors.append(f"{rel}: no se encontro el commit que lo cambio (historial incompleto)")
            continue
        old = git("show", f"{sha}^:{rel}", cwd=top)
        if old.returncode != 0:
            has_parent = git("rev-parse", "--verify", "-q", f"{sha}^", cwd=top).returncode == 0
            if has_parent:
                # El paquete anterior CREO este archivo (antes no existia en esa ruta): puede estar
                # tapando al original (p. ej. build.gradle junto a build.gradle.kts). Se elimina.
                path.unlink()
                restored.append(f"{rel} (archivo nuevo creado por el paquete anterior: eliminado)")
            else:
                restore_errors.append(f"{rel}: el commit {sha[:8]} no tiene version anterior")
            continue
        path.write_text(old.stdout, encoding="utf-8", newline="")
        restored.append(f"{rel} (version previa a {sha[:8]})")


restore_v1_files()

if "--restore-only" in sys.argv:
    print("\n=== Restauracion de archivos del paquete anterior (solo restaurar) ===")
    if not restored and not restore_errors:
        print("  (nada que restaurar: el repo ya esta limpio)")
    for r in restored:
        print(f"  [RESTAURADO] {r}")
    for e in restore_errors:
        print(f"  [ERROR]      {e}")
        print(f"::error::{e}")
    sys.exit(1 if restore_errors else 0)



def flex(snippet):
    """Regex que ignora diferencias de espacios/saltos de linea entre tokens."""
    return r"\s*".join(re.escape(tok) for tok in snippet.split())


def find_file(suffix):
    if suffix.startswith("ROOT:"):
        exact = ROOT / suffix[5:]
        return exact if exact.is_file() else None
    hits = [p for p in ROOT.rglob("*") if p.is_file() and p.as_posix().endswith(suffix)
            and "/build/" not in p.as_posix() and "/.cxx/" not in p.as_posix()]
    return hits[0] if hits else None


def patch(suffix, label, old, new, marker, count=1, regex=False, requires=None, done=None, hint=None, quiet=False, keep=False):
    path = find_file(suffix)
    if path is None:
        if not quiet:
            missing.append(f"{label}: no existe {suffix}")
        return False
    text = path.read_text(encoding="utf-8")
    if marker in text:
        skipped.append(f"{label}: ya aplicado")
        return True
    if done and re.search(flex(done), text, re.S):
        skipped.append(f"{label}: ya estaba asi")
        return True
    if requires and requires not in text:
        missing.append(f"{label}: omitido porque falta el parche previo ({requires})")
        return False
    pattern = old if regex else flex(old)
    m = re.search(pattern, text, re.S)
    if not m:
        if quiet:
            return False
        missing.append(f"{label}: no se encontro el fragmento en {suffix} (tu rama es distinta)")
        if hint:
            lines = text.splitlines()
            for i, ln in enumerate(lines):
                if re.search(hint, ln):
                    lo, hi = max(0, i - 4), min(len(lines), i + 10)
                    diag.append(f"--- {suffix} (lineas {lo + 1}-{hi}) ---\n" + "\n".join(lines[lo:hi]))
                    break
        return False
    text = re.sub(pattern, (lambda _m: _m.group(0) + new) if keep else (lambda _m: new),
                  text, count=count, flags=re.S)
    path.write_text(text, encoding="utf-8")
    applied.append(label)
    return True




def patch_any(suffix, label, variants, marker, **kw):
    """Prueba varias variantes (old, new) hasta que una encaje."""
    for i, (old, new) in enumerate(variants):
        last = i == len(variants) - 1
        if patch(suffix, label, old, new, marker, quiet=not last, **kw):
            return True
    return False


# --------------------------------------------------------------------------- MainActivity.kt
patch(
    "ui/main/MainActivity.kt", "Carpeta: permiso URI sin crash",
    old="""val contentResolver = contentResolver
        val canonicalizedUri = contentResolver.canonicalize(uri)
        if (canonicalizedUri != null) uri = canonicalizedUri

        val takeFlags = result.flags and Intent.FLAG_GRANT_READ_URI_PERMISSION
        getContentResolver().takePersistableUriPermission(uri, takeFlags)""",
    new="""org.dolphinemu.dolphinemu.utils.CrashLogger.log("onDirectorySelected uri=$uri")
        // [fix] Persist the grant on the exact URI the picker returned. canonicalize() could turn
        // a tree URI into a document URI we hold no grant for -> SecurityException -> crash.
        try {
            contentResolver.takePersistableUriPermission(
                uri!!, Intent.FLAG_GRANT_READ_URI_PERMISSION
            )
        } catch (e: SecurityException) {
            org.dolphinemu.dolphinemu.utils.Log.warning(
                "[MainActivity] Could not persist permission for $uri: ${e.message}"
            )
        } catch (e: Exception) {
            org.dolphinemu.dolphinemu.utils.Log.error(
                "[MainActivity] takePersistableUriPermission failed: ${e.message}"
            )
            android.widget.Toast.makeText(
                this, org.dolphinemu.dolphinemu.R.string.write_permission_needed,
                android.widget.Toast.LENGTH_LONG
            ).show()
            return
        }""",
    marker="[fix] Persist the grant",
)
patch(
    "ui/main/MainActivity.kt", "MainActivity: listFiles() nulo",
    old="for (f in dir.listFiles()) {",
    new="for (f in dir.listFiles() ?: emptyArray()) { // [fix-null]",
    marker="[fix-null]", count=0,
)

# --------------------------------------------------------------------------- GameFileCache.kt
patch_any(
    "model/GameFileCache.kt", "Cache: copia del set en scanLibrary",
    [
        # tu rama
        ("""val folderPathsSet = preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, HashSet())
            ?: return false""",
         """// [fix-copy] getStringSet() returns the live set owned by SharedPreferences; work on a copy.
        val folderPathsSet: MutableSet<String> = HashSet(
            preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, HashSet()) ?: return false
        )"""),
        # zip original
        ("""val folderPathsSet =
            preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, HashSet())!!""",
         """// [fix-copy] getStringSet() returns the live set owned by SharedPreferences; work on a copy.
        val folderPathsSet: MutableSet<String> = HashSet(
            preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, HashSet()) ?: HashSet()
        )"""),
    ],
    marker="[fix-copy]", hint=r"getStringSet",
)
patch(
    "model/GameFileCache.kt", "Cache: escanear solo carpetas existentes",
    old="val folderPaths = folderPathsSet.toTypedArray",
    new="val folderPaths = newFolderPaths.toTypedArray",
    marker="newFolderPaths.toTypedArray", hint=r"toTypedArray",
)

# --------------------------------------------------------------------------- GameFileCacheService.java
patch(
    "services/GameFileCacheService.java", "Servicio: addOrGet sin NPE",
    old="""synchronized (gameFileCache)
    {
      return gameFileCache.addOrGet(gamePath);
    }""",
    new="""if (gameFileCache == null) // [fix-npe]
      return null;

    synchronized (gameFileCache)
    {
      return gameFileCache.addOrGet(gamePath);
    }""",
    marker="[fix-npe]",
)

# --------------------------------------------------------------------------- EmulationFragment.kt
patch(
    "fragments/EmulationFragment.kt", "Emulacion: Surface valida + cerrar si falla el boot",
    old="""Thread({
                  NativeLibrary.SurfaceChanged(surface)
                  NativeLibrary.Run(mGamePaths, statePath)
                }, "NativeEmulation").start()""",
    new="""// [fix-surface] Capture the Surface now; the field may be nulled before the thread runs.
                val startSurface = surface
                if (startSurface == null || !startSurface.isValid) {
                    runWhenSurfaceIsValid = true
                    return
                }
                Thread({
                  NativeLibrary.SurfaceChanged(startSurface)
                  NativeLibrary.Run(mGamePaths, statePath)

                  // Run() returned without the user asking (boot failure / game ended):
                  // close the screen instead of leaving it black.
                  val activity = EmulationActivity.get()
                  if (activity != null && state != State.STOPPED) {
                    activity.runOnUiThread {
                      if (!activity.isFinishing) activity.exitEmulation()
                    }
                  }
                }, "NativeEmulation").start()""",
    marker="[fix-surface]",
)

# --------------------------------------------------------------------------- GameFileCache.cpp (JNI)
patch(
    "jni/GameList/GameFileCache.cpp", "JNI: getAllGames sin overflow de referencias",
    old="""const jobjectArray array = env->NewObjectArray(static_cast<jsize>(g_game_file_cache->GetSize()),
                                                 IDCache::sGameFile.Clazz, nullptr);
  jsize i = 0;
  g_game_file_cache->ForEach([env, array, &i](const auto& game_file) {
    env->SetObjectArrayElement(array, i++, GameFileToJava(env, game_file.get()));
  });
  return array;""",
    new="""// [fix-jni]
  if (!g_game_file_cache)
    return env->NewObjectArray(0, IDCache::sGameFile.Clazz, nullptr);

  const jsize size = static_cast<jsize>(g_game_file_cache->GetSize());
  const jobjectArray array = env->NewObjectArray(size, IDCache::sGameFile.Clazz, nullptr);
  jsize i = 0;
  g_game_file_cache->ForEach([env, array, size, &i](const auto& game_file) {
    if (i >= size)
      return;
    jobject java_game = GameFileToJava(env, game_file.get());
    env->SetObjectArrayElement(array, i++, java_game);
    // Without this, big libraries overflow the JNI local reference table.
    if (java_game)
      env->DeleteLocalRef(java_game);
  });
  return array;""",
    marker="[fix-jni]",
)
patch(
    "jni/GameList/GameFileCache.cpp", "JNI: addOrGet con cache nula",
    old="""bool cache_changed = false;
  return GameFileToJava(env,""",
    new="""if (!g_game_file_cache) // [fix-jni2]
    return nullptr;

  bool cache_changed = false;
  return GameFileToJava(env,""",
    marker="[fix-jni2]",
)

# --------------------------------------------------------------------------- MainAndroid.cpp (JNI)
patch(
    "jni/MainAndroid.cpp", "JNI: BootGame con boot nulo",
    old="""std::unique_ptr<BootParameters> boot = BootParameters::GenerateFromFile(paths, savestate);
  boot->delete_savestate = delete_savestate;
  WindowSystemInfo wsi(WindowSystemType::Android, nullptr, s_surf);
  wsi.render_surface_scale = s_scaled_density;
  if (BootManager::BootCore(std::move(boot), wsi))
  {""",
    new="""std::unique_ptr<BootParameters> boot = BootParameters::GenerateFromFile(paths, savestate);
  // [fix-boot] GenerateFromFile() returns nullptr when the file can't be opened; it used to be
  // dereferenced unchecked (crash).
  bool boot_attempted = false;
  bool boot_ok = false;
  if (boot)
  {
    boot_attempted = true;
    boot->delete_savestate = delete_savestate;
    WindowSystemInfo wsi(WindowSystemType::Android, nullptr, s_surf);
    wsi.render_surface_scale = s_scaled_density;
    boot_ok = BootManager::BootCore(std::move(boot), wsi);
  }

  if (boot_ok)
  {""",
    marker="[fix-boot]",
)
patch(
    "jni/MainAndroid.cpp", "JNI: avisar si el juego no arranca",
    old="""Core::Shutdown();
  SConfig::GetInstance().SaveSettings();
  ButtonManager::Shutdown();""",
    new="""if (!boot_ok)  // [fix-boot2]
  {
    __android_log_print(ANDROID_LOG_ERROR, DOLPHIN_TAG, "Failed to boot the selected game.");
    guard.unlock();
    MsgAlert("Dolphin",
             "The game could not be started. Check that the file is readable and that the "
             "folder permission was granted, then try again.",
             false, Common::MsgType::Critical);
    guard.lock();
  }

  if (boot_attempted)
    Core::Shutdown();
  SConfig::GetInstance().SaveSettings();
  ButtonManager::Shutdown();""",
    marker="[fix-boot2]", requires="[fix-boot]",
)
patch(
    "jni/MainAndroid.cpp", "JNI: SurfaceChanged con Surface nula",
    old="""s_surf = ANativeWindow_fromSurface(env, surf);
  if (s_surf == nullptr)""",
    new="""ANativeWindow* old_surf = s_surf;  // [fix-surf]
  s_surf = surf ? ANativeWindow_fromSurface(env, surf) : nullptr;
  if (s_surf == nullptr)""",
    marker="[fix-surf]",
)
patch(
    "jni/MainAndroid.cpp", "JNI: liberar Surface anterior",
    old="""g_renderer->ChangeSurface(s_surf);
}

JNIEXPORT void JNICALL Java_org_dolphinemu_dolphinemu_NativeLibrary_SurfaceDestroyed""",
    new="""g_renderer->ChangeSurface(s_surf);

  // [fix-surf2] release the previous window only after the renderer switched away from it
  if (old_surf && old_surf != s_surf)
    ANativeWindow_release(old_surf);
}

JNIEXPORT void JNICALL Java_org_dolphinemu_dolphinemu_NativeLibrary_SurfaceDestroyed""",
    marker="[fix-surf2]", requires="[fix-surf]",
)

# --------------------------------------------------------------------------- Rendimiento: hilos (big.LITTLE)
SRC_TUNING = Path(__file__).parent / "native_src" / "AndroidThreadTuning.h"
if SRC_TUNING.exists():
    _tuning_text = SRC_TUNING.read_text(encoding="utf-8")
else:
    _tuning_text = None
    missing.append("Rendimiento: falta .github/scripts/native_src/AndroidThreadTuning.h")

_tuning_dir = find_file("Core/Common/Thread.h")
if _tuning_text and _tuning_dir is not None:
    _dest = _tuning_dir.parent / "AndroidThreadTuning.h"
    if _dest.exists() and _dest.read_text(encoding="utf-8") == _tuning_text:
        skipped.append("Rendimiento: AndroidThreadTuning.h ya existe")
    else:
        _dest.write_text(_tuning_text, encoding="utf-8")
        applied.append("Rendimiento: AndroidThreadTuning.h (archivo nuevo)")
elif _tuning_text:
    missing.append("Rendimiento: no se encontro Core/Common/Thread.h para ubicar el header")

# Opcion de configuracion nativa (Dolphin.ini -> [Core] AndroidThreadTuning, por defecto activada)
_h_ok = patch(
    "Core/Config/MainSettings.h", "Rendimiento: opcion AndroidThreadTuning (.h)",
    old="extern const ConfigInfo<bool> MAIN_SYNC_ON_SKIP_IDLE;",
    new="extern const ConfigInfo<bool> MAIN_SYNC_ON_SKIP_IDLE;\nextern const ConfigInfo<bool> MAIN_ANDROID_THREAD_TUNING;  // [fix-cfg]",
    marker="[fix-cfg]", quiet=True,
)
_c_ok = patch(
    "Core/Config/MainSettings.cpp", "Rendimiento: opcion AndroidThreadTuning (.cpp)",
    old='const ConfigInfo<bool> MAIN_SYNC_ON_SKIP_IDLE{{System::Main, "Core", "SyncOnSkipIdle"}, true};',
    new='const ConfigInfo<bool> MAIN_SYNC_ON_SKIP_IDLE{{System::Main, "Core", "SyncOnSkipIdle"}, true};\n'
        'const ConfigInfo<bool> MAIN_ANDROID_THREAD_TUNING{{System::Main, "Core", "AndroidThreadTuning"}, true};  // [fix-cfg]',
    marker="[fix-cfg]", quiet=True,
)
CFG_OK = bool(_h_ok and _c_ok)
if not CFG_OK:
    missing.append("Rendimiento: no se pudo crear la opcion AndroidThreadTuning; los hilos se ajustaran siempre")

_inc = '#include "Core/Core.h"\n#include "Common/AndroidThreadTuning.h"  // [fix-tune-inc]'
if CFG_OK:
    _inc += '\n#include "Core/Config/MainSettings.h"'
patch(
    "Core/Core/Core.cpp", "Rendimiento: include del header de hilos",
    old='#include "Core/Core.h"', new=_inc, marker="[fix-tune-inc]",
)
_call = "Common::AndroidTuning::BoostEmuThread();"
_cond = ("if (Config::Get(Config::MAIN_ANDROID_THREAD_TUNING))\n    " + _call) if CFG_OK else _call
patch(
    "Core/Core/Core.cpp", "Rendimiento: CPU thread en nucleos rapidos",
    old='Common::SetCurrentThreadName("CPU-GPU thread");',
    new='Common::SetCurrentThreadName("CPU-GPU thread");\n  ' + _cond + '  // [fix-tune1]',
    marker="[fix-tune1]", requires="[fix-tune-inc]",
)
patch(
    "Core/Core/Core.cpp", "Rendimiento: Video thread en nucleos rapidos",
    old='Common::SetCurrentThreadName("Video thread");',
    new='Common::SetCurrentThreadName("Video thread");\n    ' + _cond.replace("\n    ", "\n      ") + '  // [fix-tune2]',
    marker="[fix-tune2]", requires="[fix-tune-inc]",
)
# Los hilos de compilacion de shaders se crean desde el hilo de video (ya fijado) y heredarian su
# mascara de CPU: se liberan para que no compitan con los hilos principales.
patch(
    "VideoCommon/AsyncShaderCompiler.cpp", "Rendimiento: include en AsyncShaderCompiler",
    old='#include "VideoCommon/AsyncShaderCompiler.h"',
    new='#include "VideoCommon/AsyncShaderCompiler.h"\n#include "Common/AndroidThreadTuning.h"  // [fix-tune-inc2]',
    marker="[fix-tune-inc2]",
)
patch(
    "VideoCommon/AsyncShaderCompiler.cpp", "Rendimiento: workers de shaders sin mascara heredada",
    old=r"void\s+AsyncShaderCompiler::WorkerThreadEntryPoint\(void\*\s*param\)\s*\{",
    new="\n  Common::AndroidTuning::BackgroundWorker();  // [fix-tune3]",
    marker="[fix-tune3]", regex=True, keep=True, requires="[fix-tune-inc2]",
)

# Hilo de envio de comandos de Vulkan ("Backend Multithreading"): tambien se crea desde el hilo de
# video, asi que heredaria los 2 nucleos rapidos y competiria con la CPU/GPU emuladas.
patch(
    "VideoBackends/Vulkan/CommandBufferManager.cpp", "Vulkan: include del header de hilos",
    old='#include "Common/Assert.h"',
    new='#include "Common/Assert.h"\n#include "Common/AndroidThreadTuning.h"  // [fix-tune-inc3]',
    marker="[fix-tune-inc3]",
)
patch(
    "VideoBackends/Vulkan/CommandBufferManager.cpp", "Vulkan: hilo de envio sin mascara heredada",
    old=r"m_submit_thread\s*=\s*std::thread\(\[this\]\(\)\s*\{",
    new="\n    Common::AndroidTuning::BackgroundWorker();  // [fix-tune4]",
    marker="[fix-tune4]", regex=True, keep=True, requires="[fix-tune-inc3]",
)

# Hilos de compilacion de shaders: 1 era demasiado lento con shaders asincronos (por defecto 3;
# en el menu, 0 = automatico).
patch(
    "Core/Config/GraphicsSettings.cpp", "Shaders: 3 hilos de compilacion por defecto",
    old='{System::GFX, "Settings", "ShaderCompilerThreads"}, 1};',
    new='{System::GFX, "Settings", "ShaderCompilerThreads"}, 3 /* [fix-threads] */};',
    marker="[fix-threads]", quiet=True,
)
patch(
    "VideoCommon/VideoConfig.cpp", "Shaders: 0 = automatico",
    old="iShaderCompilerThreads = Config::Get(Config::GFX_SHADER_COMPILER_THREADS);",
    new="iShaderCompilerThreads = Config::Get(Config::GFX_SHADER_COMPILER_THREADS);\n"
        "  if (iShaderCompilerThreads == 0)  // [fix-threads2] menu: 0 = automatic\n"
        "    iShaderCompilerThreads = -1;",
    marker="[fix-threads2]", quiet=True,
)

# ---- Opciones nuevas en el menu de Graficos
_STR_EN = """
    <string name="g20_thread_tuning">Prioritize emulation threads (fast cores)</string>
    <string name="g20_thread_tuning_description">Pins the emulated CPU and GPU threads to the fast CPU cores and raises their priority. Helps on phones with big.LITTLE processors (for example Moto G20). Applies the next time a game starts.</string>
    <string name="g20_shader_threads">Shader compiler threads</string>
    <string name="g20_shader_threads_description">0 = automatic. More threads make shaders ready sooner (fewer missing graphics and stutters) but use more CPU. Applies the next time a game starts.</string>
"""
_STR_ES = """
    <string name="g20_thread_tuning">Priorizar hilos de emulación (núcleos rápidos)</string>
    <string name="g20_thread_tuning_description">Fija los hilos de CPU y GPU emuladas a los núcleos rápidos del procesador y les sube la prioridad. Ayuda en teléfonos con procesadores big.LITTLE (por ejemplo Moto G20). Se aplica al iniciar el próximo juego.</string>
    <string name="g20_shader_threads">Hilos de compilación de shaders</string>
    <string name="g20_shader_threads_description">0 = automático. Más hilos hacen que los shaders estén listos antes (menos gráficos faltantes y tirones) pero usan más CPU. Se aplica al iniciar el próximo juego.</string>
"""
_en_ok = patch(
    "res/values/strings.xml", "Menu: textos nuevos (EN)",
    old="</resources>", new=_STR_EN + "\n</resources>", marker="g20_thread_tuning", quiet=True,
)
_es_ok = patch(
    "res/values-es/strings.xml", "Menu: textos nuevos (ES)",
    old="</resources>", new=_STR_ES + "\n</resources>", marker="g20_thread_tuning", quiet=True,
)


def _file_matches(suffix, regex):
    f = find_file(suffix)
    return bool(f and re.search(regex, f.read_text(encoding="utf-8", errors="replace"), re.S))


_menu_ok = _en_ok and _file_matches(
    "settings/model/view/SliderSetting.kt",
    r"val\s+max:\s*Int,\s*val\s+units:\s*String,\s*private\s+val\s+defaultValue:\s*Int,\s*setting:\s*Setting\?",
)
if _menu_ok:
    patch(
        "settings/ui/SettingsFragmentPresenter.kt", "Menu: opciones de rendimiento (hilos y shaders)",
        old=r"R\.string\.backend_multithreading_description,\s*true,\s*backendMultithreading\s*\)\s*\)",
        new="""

        // [fix-menu] Rendimiento (procesadores big.LITTLE / compilacion de shaders)
        val perfCoreSection = settings!!.getSection(Settings.SECTION_INI_CORE)
        sl.add(
            CheckBoxSetting(
                "AndroidThreadTuning", Settings.SECTION_INI_CORE,
                R.string.g20_thread_tuning, R.string.g20_thread_tuning_description, true,
                perfCoreSection.getSetting("AndroidThreadTuning")
            )
        )
        sl.add(
            SliderSetting(
                "ShaderCompilerThreads", Settings.SECTION_GFX_SETTINGS,
                R.string.g20_shader_threads, R.string.g20_shader_threads_description, 6, "", 3,
                gfxSection.getSetting("ShaderCompilerThreads")
            )
        )""",
        marker="[fix-menu]", regex=True, keep=True, hint=r"backend_multithreading",
    )
else:
    missing.append("Menu: no se agregaron las opciones nuevas (strings o SliderSetting distintos en tu rama)")

# --------------------------------------------------------------------------- GraphicsSettings.cpp
patch(
    "Core/Core/Config/GraphicsSettings.cpp", "Shaders asincronos por defecto",
    old='"ShaderCompilationMode"}, ShaderCompilationMode::Synchronous}',
    new='"ShaderCompilationMode"},\n    ShaderCompilationMode::AsynchronousSkipRendering}  /* [fix-shader] */',
    marker="[fix-shader]",
    done='"ShaderCompilationMode"}, ShaderCompilationMode::AsynchronousSkipRendering}',
)

# --------------------------------------------------------------------------- app/build.gradle
patch(
    "Android/app/build.gradle", "Gradle: -Ofast -> -O3",
    old='"-DCMAKE_CXX_FLAGS_RELEASE=-Ofast"',
    new='"-DCMAKE_BUILD_TYPE=Release", // [fix-flags]\n'
        '                    "-DCMAKE_C_FLAGS_RELEASE=-O3 -fno-math-errno -fno-trapping-math -DNDEBUG",\n'
        '                    "-DCMAKE_CXX_FLAGS_RELEASE=-O3 -fno-math-errno -fno-trapping-math -DNDEBUG"',
    marker="[fix-flags]", quiet=True,
)
patch(
    "Android/app/build.gradle", "Gradle: firma con fallback a debug",
    old=r"signingConfig\s+signingConfigs\.release\b(?!\s*\{)",
    new="signingConfig (signingConfigs.release.storeFile?.exists() ? signingConfigs.release : signingConfigs.debug) // [fix-sign]",
    marker="[fix-sign]", count=0, regex=True, quiet=True,
)


# =========================================================================== DIAGNOSTICO + CARPETA DE USUARIO
SRC_LOGGER = Path(__file__).parent / "crashlogger_src" / "CrashLogger.java"


def create_next_to(anchor_suffix, filename, content, label):
    anchor = find_file(anchor_suffix)
    if anchor is None:
        missing.append(f"{label}: no existe {anchor_suffix}")
        return
    dest = anchor.parent / filename
    if dest.exists():
        skipped.append(f"{label}: ya existe")
        return
    dest.write_text(content, encoding="utf-8")
    applied.append(label)


if SRC_LOGGER.exists():
    create_next_to("utils/DirectoryInitialization.java", "CrashLogger.java",
                   SRC_LOGGER.read_text(encoding="utf-8"), "Diagnostico: CrashLogger.java (archivo nuevo)")
else:
    missing.append("Diagnostico: falta .github/scripts/crashlogger_src/CrashLogger.java")

patch(
    "dolphinemu/DolphinApplication.java", "Diagnostico: instalar registro en Application",
    old="application = this;",
    new="application = this;\n    org.dolphinemu.dolphinemu.utils.CrashLogger.install(this);",
    marker="CrashLogger.install",
)
patch(
    "ui/main/MainActivity.kt", "Diagnostico: mostrar informe al abrir",
    old="super.onCreate(savedInstanceState)",
    new="super.onCreate(savedInstanceState)\n        org.dolphinemu.dolphinemu.utils.CrashLogger.showPendingReport(this)",
    marker="CrashLogger.showPendingReport", hint=r"override fun onCreate",
)
patch(
    "ui/main/MainActivity.kt", "Diagnostico: migas en onDirectorySelected",
    old=r"fun onDirectorySelected\([^)]*\)\s*\{",
    new='\n        org.dolphinemu.dolphinemu.utils.CrashLogger.log("onDirectorySelected iniciado")',
    marker='CrashLogger.log("onDirectorySelected', regex=True, keep=True, quiet=True,
)
patch(
    "model/GameFileCache.kt", "Diagnostico: migas en scanLibrary",
    old=r"fun scanLibrary\([^)]*\)[^{=]*\{",
    new='\n        org.dolphinemu.dolphinemu.utils.CrashLogger.log("scanLibrary iniciado")',
    marker='CrashLogger.log("scanLibrary', regex=True, keep=True, quiet=True,
)
patch(
    "model/GameFileCache.kt", "Diagnostico: migas en addGameFolder",
    old=r"fun addGameFolder\([^)]*\)[^{=]*\{",
    new='\n        org.dolphinemu.dolphinemu.utils.CrashLogger.log("addGameFolder: $path")',
    marker='CrashLogger.log("addGameFolder', regex=True, keep=True, quiet=True,
)

# Carpeta de usuario: respaldo si /sdcard/dolphin-mmj no se puede usar (causa probable de
# "ajustes no se guardan" y del crash al escanear: sin carpeta de usuario el nativo no se inicializa)
patch(
    "utils/DirectoryInitialization.java", "Carpeta de usuario: helper de comprobacion",
    old="private static boolean setDolphinUserDirectory(Context context)",
    new="""// [fix-userdir-helper]
  private static boolean isUsableDirectory(File dir)
  {
    try
    {
      if (!dir.isDirectory() && !dir.mkdirs())
        return false;
      File probe = new File(dir, ".write_test");
      if (!probe.exists() && !probe.createNewFile())
        return false;
      probe.delete();
      return true;
    }
    catch (Exception e)
    {
      return false;
    }
  }

  private static boolean setDolphinUserDirectory(Context context)""",
    marker="[fix-userdir-helper]",
)
patch(
    "utils/DirectoryInitialization.java", "Carpeta de usuario: respaldo en carpeta de la app",
    old="""File userPath = new File(externalPath, "dolphin-mmj");
    if (!userPath.isDirectory() && !userPath.mkdir())
    {
      return false;
    }""",
    new="""File userPath = new File(externalPath, "dolphin-mmj");
    if (!isUsableDirectory(userPath))  // [fix-userdir]
    {
      // /sdcard/dolphin-mmj is not writable on this device: fall back to the app-specific folder
      // so settings can be saved and the native side always gets a valid user directory.
      File appDir = context.getExternalFilesDir(null);
      if (appDir == null)
        appDir = context.getFilesDir();
      File fallback = new File(appDir, "dolphin-mmj");
      CrashLogger.log("ALERTA: no se pudo usar " + userPath + "; probando " + fallback);
      if (!isUsableDirectory(fallback))
      {
        CrashLogger.log("ALERTA: tampoco se pudo usar " + fallback);
        return false;
      }
      userPath = fallback;
    }
    CrashLogger.log("Directorio de usuario: " + userPath);""",
    marker="[fix-userdir]", requires="[fix-userdir-helper]", hint=r"dolphin-mmj",
)

# Ajustes: crear carpeta, registrar ruta y errores de escritura
patch(
    "settings/utils/SettingsFile.java", "Ajustes: crear carpeta y registrar ruta",
    old='writer = new PrintWriter(ini, "UTF-8");',
    new="""ini.getParentFile().mkdirs();  // [fix-ini]
      org.dolphinemu.dolphinemu.utils.CrashLogger.log("Guardando ajustes en " + ini);
      writer = new PrintWriter(ini, "UTF-8");""",
    marker="[fix-ini]", hint=r"PrintWriter",
)
patch(
    "settings/utils/SettingsFile.java", "Ajustes: registrar archivo no encontrado al LEER",
    old='Log.error("[SettingsFile] File not found: " + ini.getAbsolutePath()',
    new=('org.dolphinemu.dolphinemu.utils.CrashLogger.log("Ajustes (lectura): no existe " + ini.getAbsolutePath());\n'
         '      Log.error("[SettingsFile] File not found: " + ini.getAbsolutePath()'),
    marker="Ajustes (lectura): no existe", quiet=True,
)
patch(
    "settings/utils/SettingsFile.java", "Ajustes: registrar error al ESCRIBIR",
    old='Log.error("[SettingsFile] File not found: " + fileName',
    new=('org.dolphinemu.dolphinemu.utils.CrashLogger.log("ALERTA: Ajustes: no se pudo escribir " + ini + ": " + e.getMessage());\n'
         '      Log.error("[SettingsFile] File not found: " + fileName'),
    marker="ALERTA: Ajustes: no se pudo escribir", quiet=True,
)

# --- CAUSA CONFIRMADA por el informe del dispositivo: getExternalCacheDir() == null en el Moto G20.
#     1) GameFile.getCoverPath -> DirectoryInitialization.getCacheDirectory() hacia NPE (crash al listar juegos).
#     2) setDolphinUserDirectory() hacia "return false" ANTES de extraer Sys, crear las carpetas de usuario
#        (Config, etc.) y fijar Sys/Cache en el nativo => ajustes sin guardar, pantalla negra, crash.
patch(
    "utils/DirectoryInitialization.java", "Cache externa nula: getCacheDirectory sin NPE",
    old=r"return\s+context\.getExternalCacheDir\(\)\.getPath\(\)\s*;",
    new="""File extCache = context.getExternalCacheDir();  // [fix-cache-get]
    if (extCache == null)
      extCache = context.getCacheDir();
    return extCache.getPath();""",
    marker="[fix-cache-get]", regex=True, hint=r"getExternalCacheDir",
)
patch(
    "utils/DirectoryInitialization.java", "Cache externa nula: no abortar la inicializacion",
    old=r"File\s+cacheDir\s*=\s*context\.getExternalCacheDir\(\)\s*;\s*if\s*\(\s*cacheDir\s*==\s*null\s*\)\s*(?:\{\s*)?return\s+false\s*;(?:\s*\})?",
    new="""File cacheDir = context.getExternalCacheDir();  // [fix-cache-init]
    if (cacheDir == null)
    {
      // getExternalCacheDir() is null on some devices. Returning false here used to abort the
      // whole initialization (Sys not extracted, Config folders not created, native paths unset).
      CrashLogger.log("Aviso: getExternalCacheDir() es null; usando la cache interna");
      cacheDir = context.getCacheDir();
    }""",
    marker="[fix-cache-init]", regex=True, hint=r"getExternalCacheDir",
)
patch(
    "utils/DirectoryInitialization.java", "Diagnostico: estado de inicializacion (OK)",
    old="mDirectoryState = DirectoryInitializationState.DIRECTORIES_INITIALIZED;",
    new='CrashLogger.log("DirectoryInitialization: INICIALIZADO user=" + mUserPath);  // [fix-di-ok]\n          mDirectoryState = DirectoryInitializationState.DIRECTORIES_INITIALIZED;',
    marker="[fix-di-ok]", quiet=True,
)
patch(
    "utils/DirectoryInitialization.java", "Diagnostico: estado de inicializacion (fallo)",
    old="mDirectoryState = DirectoryInitializationState.CANT_FIND_EXTERNAL_STORAGE;",
    new='CrashLogger.log("ALERTA: DirectoryInitialization fallo (CANT_FIND_EXTERNAL_STORAGE) user=" + mUserPath);  // [fix-di-fail]\n          mDirectoryState = DirectoryInitializationState.CANT_FIND_EXTERNAL_STORAGE;',
    marker="[fix-di-fail]", quiet=True,
)

if os.environ.get("DOLPHIN_LTO") == "1":
    patch(
        "ROOT:CMakeLists.txt", "Rendimiento: LTO thin (opcional)",
        old=r"project\(\s*dolphin-emu[^)]*\)",
        new="""

# [fix-lto] Link-time optimization for Android release builds (enabled by the workflow input).
if(ANDROID)
  set(CMAKE_C_FLAGS_RELEASE "${CMAKE_C_FLAGS_RELEASE} -flto=thin")
  set(CMAKE_CXX_FLAGS_RELEASE "${CMAKE_CXX_FLAGS_RELEASE} -flto=thin")
  set(CMAKE_SHARED_LINKER_FLAGS_RELEASE "${CMAKE_SHARED_LINKER_FLAGS_RELEASE} -flto=thin")
endif()
""",
        marker="[fix-lto]", regex=True, keep=True,
    )

for _suffix in ("app/build.gradle", "app/build.gradle.kts"):
    patch(
        _suffix, f"Gradle simple ({_suffix.split('/')[-1]}): -Ofast -> -O3",
        old="-DCMAKE_CXX_FLAGS_RELEASE=-Ofast",
        new="-DCMAKE_CXX_FLAGS_RELEASE=-O3 -fno-math-errno -fno-trapping-math -DNDEBUG",
        marker="-O3 -fno-math-errno -fno-trapping-math", quiet=True,
    )

# ---- listado de archivos de build (diagnostico)
_build_dir = None
for _p in ROOT.rglob("settings.gradle*"):
    if "/Source/Android/" in _p.as_posix() + "/" and "/build/" not in _p.as_posix():
        _build_dir = _p.parent
        break
if _build_dir:
    names = sorted(x.relative_to(_build_dir).as_posix() for x in list(_build_dir.glob("*.gradle*"))
                   + list(_build_dir.glob("app/*.gradle*")) + list(_build_dir.glob("gradle/*.toml")))
    diag.append("--- archivos de build en " + _build_dir.relative_to(ROOT).as_posix() + " ---\n" + "\n".join(names))
    for _f in _build_dir.glob("app/build.gradle*"):
        lines = [l.strip() for l in _f.read_text(encoding="utf-8", errors="replace").splitlines()
                 if re.search(r"coil|okhttp|picasso|CMAKE_(C|CXX)_FLAGS|signingConfig ", l)]
        diag.append(f"--- lineas relevantes de {_f.name} ---\n" + "\n".join(lines[:25]))

# --------------------------------------------------------------------------- resumen
print("\n=== Restauracion de archivos del paquete anterior ===")
if not restored and not restore_errors:
    print("  (nada que restaurar)")
for r in restored:
    print(f"  [RESTAURADO] {r}")
for e in restore_errors:
    print(f"  [ERROR]      {e}")
    print(f"::error::{e}")
print("\n=== Resumen de correcciones ===")
for a in applied:
    print(f"  [OK]      {a}")
for s in skipped:
    print(f"  [=]       {s}")
for m in missing:
    print(f"  [OMITIDO] {m}")
    print(f"::warning::{m}")

if diag:
    print("\n=== Diagnostico: como se ve ese codigo en tu rama (para adaptar los parches omitidos) ===")
    for d in diag:
        print(d)
        print()

if restore_errors:
    print("\nNo se pudieron restaurar algunos archivos del paquete anterior. "
          "Restauralos a mano desde el historial de tu fork y vuelve a ejecutar.")
    sys.exit(1)
