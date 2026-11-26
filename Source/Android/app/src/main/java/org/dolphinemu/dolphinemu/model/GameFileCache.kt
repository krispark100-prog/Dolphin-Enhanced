package org.dolphinemu.dolphinemu.model

import android.content.Context
import android.preference.PreferenceManager
import org.dolphinemu.dolphinemu.services.GameFileCacheService
import org.dolphinemu.dolphinemu.utils.ContentHandler
import java.io.File

class GameFileCache {
    init {
        init()
    }

    /**
     * Scans through the file system and updates the cache to match.
     *
     * @return true if the cache was modified
     */
    fun scanLibrary(context: Context?): Boolean {
        val preferences = PreferenceManager.getDefaultSharedPreferences(context)
        // getStringSet() returns the live set owned by SharedPreferences; mutating it is
        // undefined behaviour (and silently drops changes), so always work on a copy.
        val folderPathsSet: MutableSet<String> = HashSet(
            preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, emptySet()) ?: emptySet()
        )

        // get paths from game files
        val gameFiles = GameFileCacheService.getAllGameFiles()
        for (f in gameFiles) {
            val filename = f.getPath()
            val lastSep = filename.lastIndexOf(File.separator)
            if (lastSep > 0) {
                val path = filename.substring(0, lastSep)
                if (!folderPathsSet.contains(path)) {
                    folderPathsSet.add(path)
                }
            }
        }

        // remove non exists paths
        val newFolderPaths: MutableSet<String> = HashSet()
        for (folderPath in folderPathsSet) {
            val exists = try {
                if (folderPath.startsWith("content://")) ContentHandler.exists(folderPath)
                else File(folderPath).exists()
            } catch (e: Exception) {
                false
            }
            if (exists) {
                newFolderPaths.add(folderPath)
            }
        }

        // apply changes
        if (folderPathsSet.size != newFolderPaths.size) {
            // One or more folders are being deleted
            val editor = preferences.edit()
            editor.putStringSet(GAME_FOLDER_PATHS_PREFERENCE, newFolderPaths)
            editor.apply()
        }

        // Scan only folders that still exist (previously the stale set was passed to native code).
        val folderPaths = newFolderPaths.toTypedArray<String>()
        var cacheChanged = update(folderPaths)
        cacheChanged = cacheChanged or updateAdditionalMetadata()
        if (cacheChanged) {
            save()
        }
        return cacheChanged
    }

    @Synchronized
    external fun getAllGames(): Array<GameFile>

    @Synchronized
    external fun addOrGet(gamePath: String): GameFile?

    @Synchronized
    private external fun update(folderPaths: Array<String>): Boolean

    @Synchronized
    private external fun updateAdditionalMetadata(): Boolean

    @Synchronized
    external fun load(): Boolean

    @Synchronized
    private external fun save(): Boolean

    companion object {
        private const val GAME_FOLDER_PATHS_PREFERENCE = "gameFolderPaths"

        fun addGameFolder(path: String, context: Context?) {
            val preferences = PreferenceManager.getDefaultSharedPreferences(context)
            val folderPaths: MutableSet<String> = HashSet(
                preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, emptySet()) ?: emptySet()
            )
            if (!folderPaths.contains(path)) {
                folderPaths.add(path)
                val editor = preferences.edit()
                editor.putStringSet(GAME_FOLDER_PATHS_PREFERENCE, folderPaths)
                editor.apply()
            }
        }

        @JvmStatic
        private external fun init()
    }
}
