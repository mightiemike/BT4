[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L187-197)
```java
  public static Transaction truncateSignatures(Transaction trx) {
    Transaction.Builder builder = trx.toBuilder().clearSignature();
    for (ByteString sig : trx.getSignatureList()) {
      if (sig.size() > PER_SIGN_LENGTH) {
        builder.addSignature(ByteString.copyFrom(sig.substring(0, PER_SIGN_LENGTH).toByteArray()));
      } else {
        builder.addSignature(sig);
      }
    }
    return builder.build();
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L246-253)
```java
        if (trx.getSignatureCount() > 0) {
          List<ByteString> approveList = new ArrayList<>();
          long currentWeight = TransactionCapsule.checkWeight(permission, trx.getSignatureList(),
              Sha256Hash.hash(CommonParameter.getInstance()
                  .isECKeyCryptoEngine(), trx.getRawData().toByteArray()), approveList);
          tswBuilder.addAllApprovedList(approveList);
          tswBuilder.setCurrentWeight(currentWeight);
        }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L16-60)
```java
package org.tron.core.capsule;

import static org.tron.common.utils.StringUtil.encode58Check;
import static org.tron.common.utils.WalletUtil.checkPermissionOperations;
import static org.tron.core.Constant.MAX_CONTRACT_RESULT_SIZE;
import static org.tron.core.exception.P2pException.TypeEnum.PROTOBUF_ERROR;

import com.google.common.primitives.Bytes;
import com.google.protobuf.Any;
import com.google.protobuf.ByteString;
import com.google.protobuf.CodedInputStream;
import com.google.protobuf.CodedOutputStream;
import com.google.protobuf.GeneratedMessageV3;
import com.google.protobuf.Internal;
import com.google.protobuf.InvalidProtocolBufferException;
import com.google.protobuf.UnknownFieldSet;
import java.io.IOException;
import java.security.SignatureException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import lombok.Getter;
import lombok.Setter;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.ArrayUtils;
import org.tron.common.crypto.ECKey.ECDSASignature;
import org.tron.common.crypto.Rsv;
import org.tron.common.crypto.SignInterface;
import org.tron.common.crypto.SignUtils;
import org.tron.common.es.ExecutorServiceManager;
import org.tron.common.overlay.message.Message;
import org.tron.common.parameter.CommonParameter;
import org.tron.common.utils.ByteArray;
import org.tron.common.utils.ForkController;
import org.tron.common.utils.ReflectUtils;
import org.tron.common.utils.Sha256Hash;
import org.tron.core.actuator.TransactionFactory;
import org.tron.core.config.Parameter;
import org.tron.core.db.TransactionContext;
import org.tron.core.db.TransactionTrace;
import org.tron.core.exception.BadItemException;
```
