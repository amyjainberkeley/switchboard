import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# An accounts file is not written into a folder a sync service copies off the Mac, by any of the names those folders
# have: a service's folder in the home folder with a word after its name (Dropbox (Personal), Dropbox (Company),
# OneDrive - Company, Google Drive (old)), and the folders macOS keeps for them under ~/Library/CloudStorage, each
# said by the service's own name. A folder whose name only starts with the same letters is not one.
import os
import shutil
import tempfile
import unittest

from switchboard import backup


class Names(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='sb-synced-')
        self.addCleanup(shutil.rmtree, self.home, True)

    def said(self, rel):
        folder = os.path.join(self.home, rel)
        os.makedirs(folder, exist_ok=True)
        return backup.synced_folder(os.path.join(folder, 'f.sbx'), home=self.home)

    def test_a_services_folder_in_the_home_folder_with_a_word_after_its_name(self):
        for rel, want in (('Dropbox (Personal)', 'Dropbox'), ('Dropbox (Company)/backups', 'Dropbox'),
                          ('Dropbox-Old', 'Dropbox'), ('Google Drive (old)', 'Google Drive'),
                          ('OneDrive - Northwind', 'OneDrive'), ('OneDrive (Personal)', 'OneDrive'),
                          ('Box Sync', 'Box'), ('Box (Work)', 'Box'), ('pCloud Drive (2)', 'pCloud Drive'),
                          ('iCloud Drive (Archive)', 'iCloud Drive'), ('Sync (2)', 'Sync'),
                          ('dropbox (personal)', 'Dropbox')):
            with self.subTest(rel=rel):
                self.assertEqual(self.said(rel), want)

    def test_a_name_that_only_starts_with_the_same_letters_is_not_one(self):
        for rel in ('Boxes', 'Boxing notes', 'Syncopation', 'Dropboxes are fine', 'Downloads', 'OneDriveless'):
            with self.subTest(rel=rel):
                self.assertIsNone(self.said(rel))

    def test_the_folders_macos_keeps_for_them_by_the_services_own_name(self):
        for rel, want in (('Library/CloudStorage/Dropbox-Personal', 'Dropbox'),
                          ('Library/CloudStorage/OneDrive-SharedLibraries-Northwind', 'OneDrive'),
                          ('Library/CloudStorage/Box-Box', 'Box'),
                          ('Library/CloudStorage/pCloudDrive-x', 'pCloud Drive'),
                          ('Library/CloudStorage/ProtonDrive-x@mail.example-folder', 'Proton Drive'),
                          ('Library/CloudStorage/SynologyDrive-home', 'Synology Drive'),
                          ('Library/CloudStorage/Nextcloud-x@cloud.example', 'Nextcloud'),
                          ('Library/CloudStorage/iCloudDrive', 'iCloud Drive'),
                          ('Library/CloudStorage/SomethingNew-x', 'SomethingNew'),
                          ('Library/CloudStorage', 'a sync service')):
            with self.subTest(rel=rel):
                self.assertEqual(self.said(rel), want)


if __name__ == '__main__':
    unittest.main()
