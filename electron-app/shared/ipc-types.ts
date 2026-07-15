export type FileAccessErrorCode =
  | "invalid_sender"
  | "invalid_argument"
  | "unauthorized"
  | "not_found"
  | "not_file"
  | "not_directory"
  | "too_large"
  | "unsupported_type"
  | "permission_denied"
  | "io_error";

export type FileAccessError = {
  code: FileAccessErrorCode;
  message: string;
  path?: string;
};

export type FileAccessResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: FileAccessError };

export type FilePathStat = {
  path: string;
  isFile: boolean;
  isDirectory: boolean;
  size: number;
};
