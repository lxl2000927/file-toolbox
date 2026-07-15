import os
import tempfile
import threading
import unittest

from pypdf import PdfReader, PdfWriter

from src.utils.pdf_output import PdfOutputJob, write_pdf_output_jobs


class PdfOutputTests(unittest.TestCase):
    @staticmethod
    def _write_pdf(path: str, page_count: int) -> None:
        writer = PdfWriter()
        for _ in range(page_count):
            writer.add_blank_page(width=100, height=100)
        with open(path, "wb") as output:
            writer.write(output)

    def test_concurrent_jobs_reserve_distinct_output_paths(self):
        with tempfile.TemporaryDirectory(prefix="file-toolbox-output-") as temp_dir:
            first_input = os.path.join(temp_dir, "first.pdf")
            second_input = os.path.join(temp_dir, "second.pdf")
            self._write_pdf(first_input, 1)
            self._write_pdf(second_input, 2)
            start = threading.Barrier(2)
            results: list[tuple[int, str]] = []
            errors: list[BaseException] = []

            def worker(path: str, page_count: int) -> None:
                try:
                    start.wait()
                    outputs = write_pdf_output_jobs(
                        path,
                        output_dir=temp_dir,
                        jobs=[PdfOutputJob("same.pdf", list(range(page_count)))],
                    )
                    results.append((page_count, outputs[0]))
                except BaseException as exc:
                    errors.append(exc)

            threads = [
                threading.Thread(target=worker, args=(first_input, 1)),
                threading.Thread(target=worker, args=(second_input, 2)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            paths = [path for _, path in results]
            self.assertEqual(len(set(paths)), 2)
            self.assertEqual({os.path.basename(path) for path in paths}, {"same.pdf", "same_2.pdf"})
            for expected_pages, path in results:
                self.assertEqual(len(PdfReader(path).pages), expected_pages)

    def test_cancel_after_first_job_preserves_completed_output_without_temp_files(self):
        with tempfile.TemporaryDirectory(prefix="file-toolbox-output-cancel-") as temp_dir:
            pdf_path = os.path.join(temp_dir, "input.pdf")
            self._write_pdf(pdf_path, 3)
            cancelled = False

            def on_output(_path: str, _pages: list[int], _elapsed: float) -> None:
                nonlocal cancelled
                cancelled = True

            outputs = write_pdf_output_jobs(
                pdf_path,
                output_dir=temp_dir,
                jobs=[
                    PdfOutputJob("part-1.pdf", [0]),
                    PdfOutputJob("part-2.pdf", [1, 2]),
                ],
                cancel_check=lambda: cancelled,
                on_output=on_output,
                cleanup_outputs_on_cancel=False,
            )

            self.assertEqual([os.path.basename(path) for path in outputs], ["part-1.pdf"])
            self.assertTrue(os.path.exists(outputs[0]))
            self.assertFalse(os.path.exists(os.path.join(temp_dir, "part-2.pdf")))
            self.assertEqual([name for name in os.listdir(temp_dir) if ".tmp" in name], [])


if __name__ == "__main__":
    unittest.main()
